from functools import partial
from itertools import chain
import os

import sublime
from sublime_plugin import TextCommand, WindowCommand

from . import diff
from .navigate import GsNavigate
from ..fns import filter_, pairwise
from ..git_command import GitCommand
from ..parse_diff import SplittedDiff
from ..runtime import enqueue_on_worker
from ..utils import flash
from ..view import clamp, replace_view_content
from ...common import util


__all__ = (
    "gs_line_history",
    "gs_open_line_history",
    "gs_line_history_open_commit",
    "gs_line_history_open_graph_context",
    "gs_line_history_navigate",
)


MYPY = False
if MYPY:
    from typing import List, Tuple
    from ..types import LineNo

    LineRange = Tuple[LineNo, LineNo]


class gs_line_history(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return

        settings = view.settings()
        if settings.get("git_savvy.show_file_at_commit_view"):
            self.from_historical_file(view, window)
            return

        frozen_sel = [r for r in view.sel()]
        cursor = frozen_sel[0].b if len(frozen_sel) == 1 else None
        if cursor and view.match_selector(cursor, "git-savvy.diff"):
            self.from_diff(view, window, frozen_sel[0])
        else:
            self.from_ordinary_view(view, window)

    def from_ordinary_view(self, view, window):
        repo_path = self.repo_path
        file_path = view.file_name()
        if not file_path:
            flash(view, "Not available for unsaved files.")
            return

        if view.is_dirty():
            flash(view, "Hint: For unsaved files the line selection is probably not correct.")

        ranges = [
            (line_on_point(view, s.begin()), line_on_point(view, s.end()))
            for s in view.sel()
        ]
        if not ranges:
            flash(view, "No cursor to compute a line range from.")
            return

        diff = self.no_context_diff(None, "HEAD", file_path)
        ranges = [
            (
                self.adjust_line_according_to_diff(diff, a),
                self.adjust_line_according_to_diff(diff, b)
            )
            for a, b in ranges
        ]

        window.run_command("gs_open_line_history", {
            "repo_path": repo_path,
            "file_path": file_path,
            "ranges": ranges,
        })

    def from_diff(self, view, window, first_sel):
        # type: (sublime.View, sublime.Window, sublime.Region) -> None
        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")

        d = SplittedDiff.from_view(view)
        hunk = d.hunk_for_pt(first_sel.begin())
        if not hunk:
            flash(view, "Not on a hunk.")
            return
        header = d.head_for_hunk(hunk)
        file_path = header.from_filename()
        if not file_path:
            flash(view, "Can't extract a file path.")
            return
        commit_hash = (
            d.commit_hash_before_pt(first_sel.begin())
            or settings.get("git_savvy.diff_view.target_commit")
        )

        if first_sel.empty():
            # Assume the changed lines are selected which gives slightly
            # better results than selecting the whole hunk.
            changed_lines = [line for line in hunk.content().lines() if not line.is_context()]
            first_sel = sublime.Region(changed_lines[0].a, changed_lines[-1].b)

        if d.hunk_for_pt(first_sel.end()) != hunk:
            flash(view, "Not across multiple hunks.")
            return

        hunk_with_linenos = list(diff.recount_lines(hunk))
        rel_begin, _ = diff.row_offset_and_col_in_hunk(view, hunk, first_sel.begin())
        rel_end, _ = diff.row_offset_and_col_in_hunk(view, hunk, first_sel.end())
        # The hunk includes the `@@` line but `hunk_with_linenos` does not!
        # So generally shift by `- 1`.
        # Also generally `clamp` in case the user is on the `@@` line
        # or at EOF position.
        clamp_ = partial(clamp, 0, len(hunk_with_linenos) - 1)  # type: partial[int]
        rel_begin = clamp_(rel_begin - 1)
        rel_end = clamp_(rel_end - 1)

        # Choose between a and b.  For non-historical diffs use
        # the "a" metadata as on the "b" side is the uncommitted
        # version of the file.
        if not commit_hash:
            ranges = [(
                hunk_with_linenos[rel_begin][1].a,
                hunk_with_linenos[rel_end][1].a
            )]
        else:
            ranges = [(
                hunk_with_linenos[rel_begin][1].b,
                hunk_with_linenos[rel_end][1].b
            )]

        window.run_command("gs_open_line_history", {
            "repo_path": repo_path,
            "file_path": os.path.join(repo_path, file_path),
            "ranges": ranges,
            "commit": commit_hash,
        })

    def from_historical_file(self, view, window):
        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        ranges = [
            (line_on_point(view, s.begin()), line_on_point(view, s.end()))
            for s in view.sel()
        ]
        if not ranges:
            flash(view, "No cursor to compute a line range from.")
            return

        window.run_command("gs_open_line_history", {
            "repo_path": repo_path,
            "file_path": file_path,
            "ranges": ranges,
            "commit": commit_hash,
        })


def line_on_point(view, pt):
    # type: (sublime.View, int) -> LineNo
    row, _ = view.rowcol(pt)
    return row + 1


class gs_open_line_history(WindowCommand, GitCommand):
    def run(self, repo_path, file_path, ranges, commit=None):
        # type: (str, str, List[LineRange], str) -> None
        view = util.view.get_scratch_view(self, "line_history")
        settings = view.settings()
        settings.set("git_savvy.repo_path", repo_path)
        settings.set("git_savvy.file_path", file_path)

        settings.set("result_file_regex", diff.FILE_RE)
        settings.set("result_line_regex", diff.LINE_RE)
        settings.set("result_base_dir", repo_path)

        rel_file_path = os.path.relpath(file_path, repo_path)
        title = ''.join(
            [
                'LOG: {}'.format(
                    rel_file_path
                    + ('@{}'.format(commit) if commit else '')
                )
            ]
            + [' L{}-{}'.format(lr[0], lr[1]) for lr in ranges]
        )
        view.set_name(title)
        view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.sublime-syntax")

        def render():
            normalized_short_filename = rel_file_path.replace('\\', '/')
            cmd = (
                [
                    'log',
                    '--decorate',
                    "--format=fuller",
                ]
                + [
                    '-L{},{}:{}'.format(lr[0], lr[1], normalized_short_filename)
                    for lr in ranges
                ]
                + [commit]
            )
            output = self.git(*cmd)
            replace_view_content(view, output)

        enqueue_on_worker(render)


class gs_line_history_open_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return

        diff = SplittedDiff.from_view(view)
        commits = filter_(diff.commit_hash_before_pt(s.begin()) for s in view.sel())
        for c in commits:
            window.run_command('gs_show_commit', {'commit_hash': c})


class gs_line_history_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return

        commit_hash = SplittedDiff.from_view(view).commit_hash_before_pt(view.sel()[0].begin())
        if commit_hash:
            window.run_command("gs_graph", {
                "all": True,
                "follow": self.get_short_hash(commit_hash)
            })


class gs_line_history_navigate(GsNavigate):
    offset = 0
    wrap_with_force = True

    def get_available_regions(self):
        commit_starts = self.view.find_by_selector("meta.commit-info.header")
        commits = [
            sublime.Region(a.a, b.a - 1)
            for a, b in pairwise(
                chain(
                    commit_starts,
                    [sublime.Region(self.view.size())]
                )
            )
        ]
        if not self.forward:
            return commits

        hunk_starts = self.view.find_by_selector("meta.diff.range.unified")
        # This is slightly more complicated compared to the above `commits`
        # as the hunk ends either at the next hunk *or* next commit.
        hunks = [
            sublime.Region(a.a, b.a - 1)
            for (a, type_), (b, _) in pairwise(
                chain(
                    sorted(chain(
                        ((r, "hunk") for r in hunk_starts),
                        ((r, "commit") for r in commit_starts),
                    )),
                    [(sublime.Region(self.view.size()), "end")]
                )
            )
            if type_ == "hunk"
        ]
        return sorted(hunks + commits)

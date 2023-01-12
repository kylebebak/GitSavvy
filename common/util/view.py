import bisect

import sublime
from ...core.settings import GitSavvySettings
from ...core.view import place_view


MYPY = False
if MYPY:
    from typing import Optional


##############
# DECORATORS #
##############

def single_cursor_pt(run):
    def decorated_run(self, *args, **kwargs):
        view = self.view if hasattr(self, "view") else self.window.active_view()
        cursors = view.sel()
        if not cursors:
            return

        return run(self, cursors[0].a, *args, **kwargs)
    return decorated_run


def single_cursor_coords(run):
    def decorated_run(self, *args, **kwargs):
        view = self.view if hasattr(self, "view") else self.window.active_view()
        cursors = view.sel()
        if not cursors:
            return
        coords = view.rowcol(cursors[0].a)

        return run(self, coords, *args, **kwargs)

    return decorated_run


#############################
# NEW-VIEW HELPER FUNCTIONS #
#############################

# https://github.com/sublimehq/sublime_text/issues/5772
SUBLIME_HAS_NEW_VIEW_PLACEMENT_BUG = int(sublime.version()) < 4144


def create_scratch_view(window, name, read_only=True):
    # type: (sublime.Window, str, bool) -> sublime.View
    """
    Create and return a read-only view.
    """
    if SUBLIME_HAS_NEW_VIEW_PLACEMENT_BUG:
        active_view = window.active_view()
        view = window.new_file()
        if active_view:
            place_view(window, view, after=active_view)
    else:
        view = window.new_file()
    view.settings().set("git_savvy.{}_view".format(name), True)
    view.set_scratch(True)
    view.set_read_only(read_only)
    return view


def get_is_view_of_type(view, typ):
    """
    Determine if view is of specified type.
    """
    return not not view.settings().get("git_savvy.{}_view".format(typ))


##########
# GLOBAL #
##########

def refresh_gitsavvy_interfaces(
    window,
    refresh_sidebar=False,
    refresh_status_bar=True,
):
    # type: (Optional[sublime.Window], bool, bool) -> None
    """
    Looks for GitSavvy interface views in the current window and refresh them.

    Note that it only refresh visible views.
    Other views will be refreshed when activated.
    """
    if window is None:
        return

    if refresh_sidebar:
        window.run_command("refresh_folder_list")
    if refresh_status_bar:
        av = window.active_view()
        if av:
            av.run_command("gs_update_status")

    for group in range(window.num_groups()):
        view = window.active_view_in_group(group)
        if view.settings().get("git_savvy.interface") is not None:
            view.run_command("gs_interface_refresh")

        if view.settings().get("git_savvy.log_graph_view", False):
            view.run_command("gs_log_graph_refresh")


def refresh_gitsavvy(
    view,
    refresh_sidebar=False,
    refresh_status_bar=True,
):
    # type: (Optional[sublime.View], bool, bool) -> None
    """
    Called after GitSavvy action was taken that may have effected the
    state of the Git repo.
    """
    if view is None:
        return

    if view.settings().get("git_savvy.interface") is not None:
        view.run_command("gs_interface_refresh")

    if view.settings().get("git_savvy.log_graph_view", False):
        view.run_command("gs_log_graph_refresh")

    if view.window() and refresh_status_bar:
        view.run_command("gs_update_status")

    window = view.window()
    if window and refresh_sidebar:
        window.run_command("refresh_folder_list")


def handle_closed_view(view):
    # type: (sublime.View) -> None
    if view.settings().get("git_savvy.interface") is not None:
        view.run_command("gs_interface_close")
    if view.settings().get("git_savvy.edit_view"):
        view.run_command("gs_edit_view_close")


############################
# IN-VIEW HELPER FUNCTIONS #
############################

def _region_within_regions(all_outer, inner):
    for outer in all_outer:
        if outer.begin() <= inner.begin() and outer.end() >= inner.end():
            return True
    return False


def get_lines_from_regions(view, regions, valid_ranges=None):
    if valid_ranges == []:
        return []

    line_regions = (line for region in regions for line in view.lines(region))

    valid_regions = ([region for region in line_regions if _region_within_regions(valid_ranges, region)]
                     if valid_ranges else
                     line_regions)

    return [view.substr(region) for region in valid_regions]


def get_instance_before_pt(view, pt, pattern):
    instances = tuple(region.a for region in view.find_all(pattern))
    instance_index = bisect.bisect(instances, pt) - 1
    return instances[instance_index] if instance_index >= 0 else None


def get_instance_after_pt(view, pt, pattern):
    instances = tuple(region.a for region in view.find_all(pattern))
    instance_index = bisect.bisect(instances, pt)
    return instances[instance_index] if instance_index < len(instances) else None


#################
# MISCELLANEOUS #
#################

def disable_other_plugins(view):
    # Disable key-bindings for Vitageous
    # https://github.com/guillermooo/Vintageous/wiki/Disabling
    if GitSavvySettings().get("vintageous_friendly", False) is False:
        view.settings().set("__vi_external_disable", False)


def mark_as_lintable(view):
    view.settings().set("SublimeLinter.enabled?", True)

"""The editor's own settings / data folder: %LOCALAPPDATA%\\MXL Capybara Editor, instead of the collector's
%LOCALAPPDATA%\\MXL Item Collector.

The collector's paths module reads the environment variable MXL_USER_DIR before its default, so setup() only has to
set it, first thing in the launcher (before paths / settings are imported, and before the splash process is started:
it inherits the variable). A MXL_USER_DIR set by the user is kept.

First start of this version: the collector's folder (settings.json, the game data extracted from the game, the D2
look art cache, the log of 1.1) is copied over, so nothing has to be set or extracted again. The copy is made in a
temporary folder renamed at the end: an interrupted copy is started again next time, never half used (a failed one
is logged, and the editor starts with an empty folder). Without a collector folder, the new folder is made empty and
the collector's first-start steps run as for any new user. The
collector's folder itself is left as it was (an installed MXL Item Collector still uses it)."""
import os
import shutil

FOLDER_NAME = 'MXL Capybara Editor'
OLD_FOLDER_NAME = 'MXL Item Collector'
SKIP = {'data.old'}                       # the collector's backup of an earlier extraction: not needed here

migrated_from = None                      # the collector's folder when it was copied at this start (for the log)
migrate_error = None


def _base():
    return os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')


def setup():
    """Sets MXL_USER_DIR to the editor's own folder (copying the collector's on the first start). Never raises."""
    global migrated_from, migrate_error
    if os.environ.get('MXL_USER_DIR'):
        return os.environ['MXL_USER_DIR']
    new = os.path.join(_base(), FOLDER_NAME)
    old = os.path.join(_base(), OLD_FOLDER_NAME)
    if not os.path.isdir(new) and os.path.isdir(old):
        tmp = new + '.copying'
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(old, tmp, ignore=lambda folder, names: [n for n in names if n in SKIP and folder == old])
            os.replace(tmp, new)
            migrated_from = old
        except Exception as err:          # the editor still starts, with a new empty folder
            migrate_error = f'{type(err).__name__}: {err}'
            shutil.rmtree(tmp, ignore_errors=True)
    try:
        os.makedirs(new, exist_ok=True)
    except OSError:
        pass
    os.environ['MXL_USER_DIR'] = new
    return new

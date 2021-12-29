import argparse
import math
import os
import pathlib
import shutil


def app_path_arg_validate(path):
    path = os.path.abspath(str(path))
    if not os.access(path, os.F_OK | os.R_OK | os.X_OK):
        raise argparse.ArgumentTypeError(
                f"Specified application could not be found: {path}")
    return pathlib.Path(path)


def add_app_path_arg(parser, *, app):
    try:
        default = app_path_arg_validate(shutil.which(app))
    except argparse.ArgumentTypeError:
        default = None
    except TypeError:
        default = None
    except ValueError:
        default = None

    if default:
        help = f"Specify path for '{app}' application (optional)"
        required = False
    else:
        help = f"Specify path for '{app}' application"
        required = True

    parser.add_argument(
            f"--path-{app}",
            help=help,
            type=app_path_arg_validate,
            dest=f"path_{app}",
            default=default,
            required=required)


# Safe conversion of logarithm to floor integer value
def safe_int_log(value, base):
    log = int(math.floor(math.log(value, base)))
    while base ** log > value:
        log -= 1
    while base ** (log + 1) <= value:
        log += 1
    return log


__all__ = ["add_app_path_arg", "safe_int_log"]

import argparse
import logging
import math
import os
import pathlib
import shutil
import subprocess


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


def run_and_parse_font_config_binary(binary, *, args, fields):
    sep = "\n/\1/\n"
    format_string = sep.join(["%{" + field + "}" for field in fields])

    output = subprocess.check_output(
            [binary] + args + ["--format", format_string])
    output = output.decode("utf-8")
    values = output.split(sep)

    if len(values) != len(fields):
        raise argparse.ArgumentTypeError(
                f"Unable to parse fontconfig binary {binary} output: "
                f"expected {len(fields)} values for fields {fields}, but "
                f"got {len(values)}. Raw output: {output}.")

    return dict(zip(fields, values))


def font_file_arg(path):
    if pathlib.Path(path).exists():
        return path.resolve()

    path_fc_pattern = shutil.which("fc-pattern")
    path_fc_match = shutil.which("fc-match")
    if not path_fc_pattern or not path_fc_match:
        raise argparse.ArgumentTypeError(
                f"Provided value ({path}) is not a file. Cannot parse as "
                f"[fontconfig] pattern because [fc-pattern] and [fc-match] "
                f"are not in path.")

    parsed_pattern = run_and_parse_font_config_binary(
            path_fc_pattern,
            args=[path],
            fields=["family", "style"])
    best_guess = run_and_parse_font_config_binary(
            path_fc_match,
            args=[path],
            fields=["file", "family", "style"])

    file = best_guess["file"]

    def parse_style(style):
        style = style.lower()
        if "regular" in style.split(","):
            return ""

        return style

    best_guess["style"] = parse_style(best_guess["style"])
    parsed_pattern["style"] = parse_style(parsed_pattern["style"])

    if best_guess["family"] != parsed_pattern["family"] or \
       best_guess["style"] != parsed_pattern["style"]:
        logging.warn(
                f"Using font {best_guess['family']} ({best_guess['style']}) "
                f"from {file}, which approximately matches provided "
                f"query ({path}) which was interpretted as "
                f"{parsed_pattern['family']} ({parsed_pattern['style']})")

    return file


# Safe conversion of logarithm to floor integer value
def safe_int_log(value, base):
    log = int(math.floor(math.log(value, base)))
    while base ** log > value:
        log -= 1
    while base ** (log + 1) <= value:
        log += 1
    return log


__all__ = ["add_app_path_arg", "safe_int_log"]

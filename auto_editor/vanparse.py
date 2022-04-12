import re
import sys
import difflib
import textwrap
from dataclasses import dataclass
from shutil import get_terminal_size

from typing import List, Sequence, Optional, Any, Union

from auto_editor.utils.log import Log


@dataclass
class Required:
    names: Sequence[str]
    nargs: Union[int, str] = "*"
    type: type = str
    choices: Optional[Sequence[str]] = None
    help: str = ""
    _type: str = "required"


@dataclass
class Options:
    names: Sequence[str]
    nargs: Union[int, str] = 1
    type: type = str
    default: Optional[Union[int, str]] = None
    action: str = "default"
    choices: Optional[Sequence[str]] = None
    help: str = ""
    dataclass: Any = None
    manual: str = ""
    _type: str = "option"


@dataclass
class OptionText:
    text: str
    _type: str


def indent(text: str, prefix: str) -> str:
    def predicate(line: str) -> str:
        return line.strip()

    def prefixed_lines():
        for line in text.splitlines(True):
            yield (prefix + line if predicate(line) else line)

    return "".join(prefixed_lines())


def out(text: str) -> None:
    width = get_terminal_size().columns - 3

    indent_regex = re.compile(r"^(\s+)")
    wrapped_lines = []

    for line in text.split("\n"):
        exist_indent = re.search(indent_regex, line)
        pre_indent = exist_indent.groups()[0] if exist_indent else ""

        wrapped_lines.append(
            textwrap.fill(line, width=width, subsequent_indent=pre_indent)
        )

    print("\n".join(wrapped_lines))


def print_option_help(option: Options) -> None:
    from dataclasses import fields, _MISSING_TYPE

    text = "  " + ", ".join(option.names) + "\n    " + option.help + "\n\n"
    if option.dataclass is not None:
        text += "    Arguments:\n    "

        args = []
        for field in fields(option.dataclass):
            if field.name != "_type":
                arg = "{" + field.name
                if not isinstance(field.default, _MISSING_TYPE):
                    arg += "=" + str(field.default)
                arg += "}"
                args.append(arg)

        text += ",".join(args)

    if option.manual != "":
        text += indent(option.manual, "    ") + "\n\n"

    if option.dataclass is not None:
        pass
    elif option.action == "default":
        text += f"    type: {option.type.__name__}\n"

        if option.nargs != 1:
            text += f"    nargs: {option.nargs}\n"

        if option.default is not None:
            text += f"    default: {option.default}\n"

        if option.choices is not None:
            text += "    choices: " + ", ".join(option.choices) + "\n"
    elif option.action == "store_true":
        text += "    type: flag\n"
    else:
        text += "    type: unknown\n"

    out(text)


def print_program_help(args: List[Union[Options, Required, OptionText]]) -> None:
    text = ""
    for arg in args:
        if isinstance(arg, OptionText):
            text += f"\n  {arg.text}\n" if arg._type == "text" else "\n"
        else:
            text += "  " + ", ".join(arg.names) + f": {arg.help}\n"
    text += "\n"
    out(text)


def to_underscore(name: str) -> str:
    """Convert new style options to old style.  e.g. --hello-world -> --hello_world"""
    return name[:2] + name[2:].replace("-", "_")


def to_key(op: Union[Options, Required]) -> str:
    """Convert option name to arg key.  e.g. --hello-world -> hello_world"""
    return op.names[0][:2].replace("-", "") + op.names[0][2:].replace("-", "_")


def get_option(name: str, options: List[Options]) -> Optional[Options]:
    for option in options:
        if name in option.names or name in map(to_underscore, option.names):
            return option
    return None


class ArgumentParser:
    def __init__(
        self, program_name: str, version: str, description: Optional[str] = None
    ):
        self.program_name = program_name
        self._version = version
        self.description = description

        self.requireds: List[Required] = []
        self.options: List[Options] = []
        self.args: List[Union[Options, Required, OptionText]] = []

    def add_argument(self, *args: str, **kwargs) -> None:
        x = Options(args, **kwargs)
        self.options.append(x)
        self.args.append(x)

    def add_required(self, *args: str, **kwargs) -> None:
        x = Required(args, **kwargs)
        self.requireds.append(x)
        self.args.append(x)

    def add_text(self, text: str) -> None:
        self.args.append(OptionText(text, "text"))

    def add_blank(self) -> None:
        self.args.append(OptionText("", "blank"))

    def parse_args(self, sys_args: List[str]):
        if sys_args == [] and self.description:
            out(self.description)
            sys.exit()

        if sys_args == ["-v"] or sys_args == ["-V"]:
            out(f"{self.program_name} version {self._version}")
            sys.exit()

        return ParseOptions(sys_args, self.options, self.requireds, self.args)


def parse_dataclass(unsplit_arguments: str, dataclass: Any) -> Any:
    """
    Positional Arguments
        --rectangle 0,end,10,20,20,30,#000, ...

    Keyword Arguments
        --rectangle start=0,end=end,x1=10, ...
    """

    from dataclasses import fields

    ARG_SEP = ","
    KEYWORD_SEP = "="

    d_name = dataclass.__name__

    keys = [field.name for field in fields(dataclass)]
    kwargs = {}
    args = []

    allow_positional_args = True

    if unsplit_arguments == "":
        return dataclass()

    for i, arg in enumerate(unsplit_arguments.split(ARG_SEP)):
        if i + 1 > len(keys):
            Log().error(f"{d_name} has too many arguments, starting with '{arg}'.")

        if KEYWORD_SEP in arg:
            allow_positional_args = False

            parameters = arg.split(KEYWORD_SEP)
            if len(parameters) > 2:
                Log().error(f"{d_name} invalid syntax: '{arg}'.")
            key, val = parameters
            if key not in keys:
                Log().error(f"{d_name} got an unexpected keyword '{key}'")

            kwargs[key] = val
        elif allow_positional_args:
            args.append(arg)
        else:
            Log().error(f"{d_name} positional argument follows keyword argument.")

    try:
        dataclass_instance = dataclass(*args, **kwargs)
    except TypeError as err:
        err_list = [d_name] + str(err).split(" ")[1:]
        Log().error(" ".join(err_list))

    return dataclass_instance


class ParseOptions:
    @staticmethod
    def parse_value(option: Union[Options, Required], val: Optional[str]) -> Any:
        if val is None and option.nargs == 1:
            Log().error(f"{option.names[0]} needs argument.")

        try:
            value = option.type(val)
        except TypeError as e:
            Log().error(str(e))

        if option.choices is not None and value not in option.choices:
            my_choices = ", ".join(option.choices)

            Log().error(
                f"{value} is not a choice for {option.names[0]}\nchoices are:\n  {my_choices}"
            )

        return value

    def set_arg_list(
        self, option_list_name: Optional[str], my_list: list, list_type: Optional[type]
    ) -> None:
        assert option_list_name is not None
        if list_type is not None:
            setattr(self, option_list_name, list(map(list_type, my_list)))
        else:
            setattr(self, option_list_name, my_list)

    def __init__(
        self,
        sys_args: List[str],
        options: List[Options],
        requireds: List[Required],
        args: List[Union[Options, Required, OptionText]],
    ) -> None:

        option_names: List[str] = []

        self.help = False

        # Set default attributes
        for op in options:
            for name in op.names:
                option_names.append(name)

            if op.action == "store_true":
                value: Any = False
            elif op.nargs != 1:
                value = []
            elif op.default is None:
                value = None
            else:
                value = op.type(op.default)

            setattr(self, to_key(op), value)

        # Figure out command line options changed by user.
        used_options: List[Options] = []

        req_list = []
        req_list_name = requireds[0].names[0]
        req_list_type = requireds[0].type
        setting_req_list = requireds[0].nargs != 1

        option_list = []
        op_list_name = None
        op_list_type: Optional[type] = str
        setting_op_list = False

        i = 0
        while i < len(sys_args):
            arg = sys_args[i]
            option = get_option(arg, options)

            if option is None:
                if setting_op_list:
                    if used_options and used_options[-1].dataclass is not None:
                        op_list_type = None
                        arg = parse_dataclass(arg, used_options[-1].dataclass)

                    option_list.append(arg)

                elif requireds and not arg.startswith("--"):

                    if requireds[0].nargs == 1:
                        setattr(
                            self, req_list_name, self.parse_value(requireds[0], arg)
                        )
                        requireds.pop()
                    else:
                        req_list.append(arg)
                else:
                    label = "option" if arg.startswith("--") else "short"

                    # 'Did you mean' message might appear that options need a comma.
                    if arg.replace(",", "") in option_names:
                        Log().error(f"Option '{arg}' has an unnecessary comma.")

                    close_matches = difflib.get_close_matches(arg, option_names)
                    if close_matches:
                        Log().error(
                            f"Unknown {label}: {arg}\n\n    Did you mean:\n        "
                            + ", ".join(close_matches)
                        )
                    Log().error(f"Unknown {label}: {arg}")
            else:
                if op_list_name is not None:
                    self.set_arg_list(op_list_name, option_list, op_list_type)

                if option in used_options:
                    Log().error(f"Cannot repeat option {option.names[0]} twice.")

                used_options.append(option)

                setting_op_list = False
                option_list = []
                op_list_name = None

                key = to_key(option)

                next_arg = None if i == len(sys_args) - 1 else sys_args[i + 1]
                if next_arg == "-h" or next_arg == "--help":
                    print_option_help(option)
                    sys.exit()

                if option.nargs != 1:
                    setting_op_list = True
                    op_list_name = key
                    op_list_type = option.type
                elif option.action == "store_true":
                    value = True
                else:
                    value = self.parse_value(option, next_arg)
                    i += 1
                setattr(self, key, value)

            i += 1

        if setting_op_list:
            self.set_arg_list(op_list_name, option_list, op_list_type)

        if setting_req_list:
            self.set_arg_list(req_list_name, req_list, req_list_type)

        if self.help:
            print_program_help(args)
            sys.exit()

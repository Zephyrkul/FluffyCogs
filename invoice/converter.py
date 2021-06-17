import inspect
import re
from dataclasses import fields
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Final, Optional, Union, get_args, get_origin

from discord.ext.commands.view import StringView
from redbot.core import commands

if TYPE_CHECKING:
    AsCFIdentifier = str
else:

    def AsCFIdentifier(argument: str) -> str:
        return re.sub(r"\W+|^(?=\d)", "_", argument.casefold())


_ident_param: Final = inspect.Parameter(
    name="_", kind=inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=AsCFIdentifier
)


def _not_nonetype(item):
    return item is not type(None)


def asdict_shallow(obj, dict_factory=dict):
    """Same as dataclasses' asdict, but doesn't copy and doesn't recurse."""
    return dict_factory((f.name, getattr(obj, f.name)) for f in fields(obj))


# Theoretically this works for any well-formed dataclass-like class
# but it's designed with dataclasses in mind
class DataclassConverter:
    MISSING: ClassVar[Any] = object()
    __total__: ClassVar[bool] = True

    def __init_subclass__(cls) -> None:
        parameters = inspect.signature(cls).parameters
        if any(k != k.casefold() for k in parameters):
            raise TypeError("DataclassConverter __init__ parameters must have casefolded names.")

    @staticmethod
    async def _transform_identifier(
        ctx: commands.Context, parameters: Dict[str, inspect.Parameter], kwargs: Dict[str, Any]
    ) -> Optional[inspect.Parameter]:
        arg: str = await ctx.command.transform(ctx, _ident_param)
        try:
            parameter = parameters.pop(arg)
        except KeyError:
            if arg in kwargs:
                raise commands.BadArgument(
                    f"Multiple values provided for argument `{arg}`"
                ) from None
            raise commands.BadArgument(f"No such setting by the name of `{arg}`") from None
        anno = parameter.annotation
        if get_origin(anno) is Union:
            # since a name was passed, suppress d.py's typing.Optional behavior
            parameter = parameter.replace(
                annotation=Union[tuple(filter(_not_nonetype, get_args(anno)))]  # type: ignore
            )
        return parameter

    @classmethod
    def _maybe_fill_missing(
        cls,
        ctx: commands.Context,
        parameters: Dict[str, inspect.Parameter],
        kwargs: Dict[str, Any],
    ):
        ignore_optional_for_conversion = ctx.command.ignore_optional_for_conversion
        for name, param in parameters.items():
            if param.default is not param.empty:
                continue
            anno = param.annotation
            if (
                not ignore_optional_for_conversion
                and get_origin(anno) is Union
                and type(None) in get_args(anno)
            ):
                kwargs[name] = None
            elif cls.__total__:
                raise commands.MissingRequiredArgument(param)
            else:
                kwargs[name] = cls.MISSING

    @classmethod
    async def convert(cls, ctx: commands.Context, argument: str):
        parameters = dict(inspect.signature(cls).parameters)
        parameter = None
        kwargs: Dict[str, Any] = {}
        old_view = ctx.view
        view = StringView(argument)
        ctx.view = view
        try:
            while not view.eof:
                if parameter:
                    kwargs[parameter.name], parameter = (
                        await ctx.command.transform(ctx, parameter),
                        None,
                    )
                else:
                    parameter = await cls._transform_identifier(ctx, parameters, kwargs)
        finally:
            ctx.view = old_view
        if parameter:
            raise commands.MissingRequiredArgument(parameter)
        cls._maybe_fill_missing(ctx, parameters, kwargs)
        return cls(**kwargs)  # type: ignore

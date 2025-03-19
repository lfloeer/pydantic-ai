from __future__ import annotations as _annotations

import functools
import inspect
import sys
from pathlib import Path
from typing import Any, ClassVar, Generic, Literal, NotRequired, Self, Union

from pydantic._internal import _typing_extra

from ._utils import get_unwrapped_function_name

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup
else:
    ExceptionGroup = ExceptionGroup

import yaml
from pydantic import BaseModel, Field, ValidationError
from pydantic_core import to_json, to_jsonable_python
from typing_extensions import TypedDict, TypeVar

from .assessments.spec import Assessment, AssessmentFunction, AssessmentSpec, get_default_registry

InputsT = TypeVar('InputsT', default=dict[str, Any])
OutputT = TypeVar('OutputT', default=dict[str, Any])
MetadataT = TypeVar('MetadataT', default=dict[str, Any])

DEFAULT_DATASET_PATH = './test_cases.yaml'


class DatasetRow(BaseModel, Generic[InputsT, OutputT, MetadataT], extra='forbid'):
    """A single row of a "dataset", consisting of input, expected output, and metadata."""

    name: str
    inputs: InputsT
    metadata: MetadataT
    expected_output: OutputT | None = None
    assessments: list[AssessmentSpec] = Field(default_factory=list)


class EvaluationRow(BaseModel, Generic[InputsT, OutputT, MetadataT], extra='forbid'):
    """A single row for evaluation."""

    name: str
    inputs: InputsT
    metadata: MetadataT
    expected_output: OutputT | None
    assessments: list[Assessment[InputsT, OutputT, MetadataT]]


class Dataset(BaseModel, Generic[InputsT, OutputT, MetadataT], extra='forbid'):
    """A dataset of test cases, each consisting of input, expected output, and metadata."""

    rows: list[DatasetRow[InputsT, OutputT, MetadataT]]
    default_assessments: list[AssessmentSpec] = Field(default_factory=list)

    _assessment_registry: ClassVar[dict[str, AssessmentFunction[Any, Any, Any]]] = {}
    """This should be AssessmentFunction[InputsT, OutputT, MetadataT], but classvars can't be generic in Python."""

    def __init_subclass__(cls, **kwargs: Any):
        super().__init_subclass__(**kwargs)
        # make a copy of the registry to ensure registering functions in subclasses doesn't affect the parent classes
        cls._assessment_registry = cls._assessment_registry.copy()

    @classmethod
    def assessment_registry(cls) -> dict[str, AssessmentFunction[InputsT, OutputT, MetadataT]]:
        combined_registry: dict[str, AssessmentFunction[Any, Any, Any]] = {**get_default_registry()}
        for c in cls.__mro__[::-1]:
            combined_registry.update(getattr(c, '_assessment_registry', {}))
        return combined_registry

    @classmethod
    @functools.cache
    def _evaluation_row_type(cls) -> type[EvaluationRow[InputsT, OutputT, MetadataT]]:
        return EvaluationRow[cls._params()]  # type: ignore

    @classmethod
    @functools.cache
    def _params(cls) -> tuple[type[InputsT], type[OutputT], type[MetadataT]]:
        for c in cls.__mro__:
            metadata = getattr(c, '__pydantic_generic_metadata__')
            if len(args := (metadata.get('args', ()) or getattr(c, '__args__', ()))) == 3:
                return args
        raise ValueError(f'Could not determine the generic parameters for {cls}')

    @classmethod
    def assessment(
        cls, f: AssessmentFunction[InputsT, OutputT, MetadataT]
    ) -> AssessmentFunction[InputsT, OutputT, MetadataT]:
        """Decorator that registers an assessment function in the class-specific registry.

        This provides a generic-type-checking alternative to the `@assessment` decorator.
        """
        cls._assessment_registry[get_unwrapped_function_name(f)] = f
        return f

    def evaluation_rows(self) -> list[EvaluationRow[InputsT, OutputT, MetadataT]]:
        registry = self.assessment_registry()

        evaluation_rows: list[EvaluationRow[InputsT, OutputT, MetadataT]] = []
        errors: list[ValueError] = []
        row_type = self._evaluation_row_type()
        for row in self.rows:
            assessments: list[Assessment[Any, Any, Any]] = []
            for spec in row.assessments + self.default_assessments:
                try:
                    assessment = Assessment[InputsT, OutputT, MetadataT].from_registry(registry, row.name, spec)
                except ValueError as e:
                    errors.append(e)
                    continue
                assessments.append(assessment)
            evaluation_rows.append(
                row_type(
                    name=row.name,
                    inputs=row.inputs,
                    metadata=row.metadata,
                    expected_output=row.expected_output,
                    assessments=assessments,
                )
            )
        if errors:
            raise ExceptionGroup(f'{len(errors)} error(s) loading assessments from registry', errors[:3])
        return evaluation_rows

    # TODO: Task: Always save a schema file when saving the dataset
    def save(self, path: Path | str = DEFAULT_DATASET_PATH, schema_ref: str | None = None) -> None:
        path = self._get_relative_path(path)
        content = yaml.dump(to_jsonable_python(self), sort_keys=False)
        if schema_ref is not None:
            content = _ensure_yaml_language_server_line(content, schema_ref)
        path.write_text(content)

    @classmethod
    def from_yaml(cls, path: Path | str = DEFAULT_DATASET_PATH) -> Self:
        path = cls._get_relative_path(path)
        if not path.exists():
            raise FileNotFoundError(f'{cls.__name__} dataset file {path} does not exist')

        raw = path.read_text()
        loaded = yaml.safe_load(raw)
        try:
            result = cls.model_validate(loaded)
            # result.rows = [cls.serialized_row_type().model_validate(row.model_dump()) for row in result.rows]
        except ValidationError as e:
            raise ValueError(
                f'{cls.__name__} dataset file {path} contains data that does not match the schema:\n{e}.'
            ) from e
        return result

    @classmethod
    def generate_dataset_files(
        cls,
        dataset_path: Path | str = DEFAULT_DATASET_PATH,
        schema_path: Path | str | None = None,
    ) -> str:
        dataset_path = cls._get_relative_path(dataset_path)

        if schema_path is None:
            if dataset_path.exists():
                # Try to infer the schema path from the first line of the existing dataset file
                first_line = dataset_path.read_text().split('\n', 1)[0]
                if first_line.startswith('# yaml-language-server: $schema='):
                    schema_path = (dataset_path.parent / first_line.split('=', 1)[1]).resolve()
            if schema_path is None:
                schema_path = cls._get_schema_path(dataset_path.parent)
        else:
            schema_path = cls._get_relative_path(schema_path)

        schema_content = to_json(cls.model_json_schema_with_assessments(), indent=2).decode() + '\n'
        if not schema_path.exists() or schema_path.read_text() != schema_content:
            schema_path.write_text(schema_content)

        schema_ref = str(_get_relative_path_reference(schema_path, dataset_path.parent))
        yaml_language_server_line = f'# yaml-language-server: $schema={schema_ref}'
        if dataset_path.exists():
            try:
                cls.from_yaml(dataset_path)
            except ValueError as e:
                if isinstance(e.__cause__, ValidationError):
                    raise ValueError(
                        f'{cls.__name__} dataset file {dataset_path} already exists, but does not contain compatible data.'
                        f' Fix or delete the file before calling this function:\n{e.__cause__}'
                    ) from e.__cause__
                else:
                    raise
            dataset_text = dataset_path.read_text()
            cases_text_with_schema = _ensure_yaml_language_server_line(dataset_text, schema_ref)
            if cases_text_with_schema != dataset_text:
                dataset_path.write_text(cases_text_with_schema)
        else:
            content = yaml.dump(to_jsonable_python(cls(rows=[])), sort_keys=False)
            dataset_path.write_text(f'{yaml_language_server_line}\n{content}')
        return schema_ref

    @classmethod
    def model_json_schema_with_assessments(cls) -> dict[str, Any]:
        registry = cls.assessment_registry()

        assessment_types: list[Any] = []
        for name, function in registry.items():
            signature = inspect.signature(function)

            scoring_context_param, *other_params = signature.parameters.values()
            type_hints = _typing_extra.get_function_type_hints(function)
            type_hints.pop(scoring_context_param.name, None)
            type_hints.pop('return', None)
            required_type_hints: dict[str, Any] = {}

            for p in other_params:
                type_hints.setdefault(p.name, Any)
                if p.default is not p.empty:
                    type_hints[p.name] = NotRequired[type_hints[p.name]]
                else:
                    required_type_hints[p.name] = type_hints[p.name]

            if len(type_hints) == 0 or not required_type_hints:
                # Shortest option: just the call name
                assessment_types.append(Literal[name])
            if len(type_hints) == 1:
                # Short option: only have one parameter, so we can drop the nesting
                [type_hint_type] = type_hints.values()  # pyright: ignore
                td = TypedDict(f'short_assessment_{name}', {name: type_hint_type})  # pyright: ignore
                td.__pydantic_config__ = {'extra': 'forbid'}  # pyright: ignore
                assessment_types.append(td)
            if len(type_hints) > 1:
                if len(required_type_hints) == 1:
                    # Short option: only have one required parameter, so we can drop the nesting
                    type_hint_type = next(iter(required_type_hints.values()))  # pyright: ignore
                    td = TypedDict(f'short_assessment_{name}', {name: type_hint_type})  # pyright: ignore
                    td.__pydantic_config__ = {'extra': 'forbid'}  # pyright: ignore
                    assessment_types.append(td)

                # Long form: multiple parameters, or multiple required parameters
                params_td = TypedDict(f'assessment_params_{name}', type_hints)  # pyright: ignore
                params_td.__pydantic_config__ = {'extra': 'forbid'}  # pyright: ignore
                td = TypedDict(f'assessment_{name}', {name: params_td})  # pyright: ignore
                td.__pydantic_config__ = {'extra': 'forbid'}  # pyright: ignore
                assessment_types.append(td)
            # Note: We might want to also generate the JSON schema for the format `call: '...', args: [...], kwargs: {...}`.
            #   It would be a bit complex to implement but not impossible.

        params = cls._params()

        class ClsDatasetRow(DatasetRow[params[0], params[1], params[2]]):
            assessments: list[Union[tuple(assessment_types)]] = []  # pyright: ignore  # noqa UP007

        ClsDatasetRow.__name__ = cls.__name__ + 'Row'

        class ClsDataset(BaseModel, extra='forbid'):
            rows: list[ClsDatasetRow]
            default_assessments: list[Union[tuple(assessment_types)]] = []  # pyright: ignore  # noqa UP007

        ClsDataset.__name__ = cls.__name__

        return ClsDataset.model_json_schema()

    # TODO: Task: Uncomment and finish implementing function to generate examples for a dataset using an LLM
    # @classmethod
    # def generate_dataset_examples(
    #     cls,
    #     model: models.Model | models.KnownModelName = 'gpt-4o',
    #     min_count: int = 3,
    #     dataset_path: Path | str = DEFAULT_DATASET_PATH,
    # ):
    #     dataset_path = cls._get_relative_path(dataset_path)
    #     schema_ref = cls.generate_dataset_files(dataset_path=dataset_path, schema_path=None)
    #
    #     existing_content: str | None = None
    #
    #     try:
    #         existing_rows = cls.from_yaml(dataset_path).rows
    #         min_count = max(0, min_count - len(existing_rows))
    #         if min_count == 0:
    #             return  # nothing to do, already have enough examples
    #         if existing_rows:
    #             existing_content = dataset_path.read_text()
    #     except FileNotFoundError:
    #         pass  # in this case, we'll generate a new file, so we ignore the error
    #
    #     examples = asyncio.run(_generate_examples(cls, dataset_path, model, min_count))
    #
    #     if existing_content is None:
    #         content = yaml.dump(to_jsonable_python(cls(rows=examples)), sort_keys=False)
    #         content = _ensure_yaml_language_server_line(content, schema_ref)
    #         dataset_path.write_text(content)
    #     else:
    #         new_lines = yaml.dump(to_jsonable_python(cls(rows=examples)), sort_keys=False).splitlines()
    #         new_lines = new_lines[1:]  # drop the first line, which is the document start
    #         new_content = _ensure_yaml_language_server_line(existing_content, schema_ref)
    #         if not new_content.endswith('\n'):
    #             new_content += '\n'
    #         new_content += '\n'.join(new_lines)
    #         dataset_path.write_text(new_content)

    @classmethod
    def _get_schema_path(cls, dataset_path: Path) -> Path:
        return dataset_path.parent / f'./{dataset_path.stem}_schema.json'

    @classmethod
    def _get_relative_path(cls, path: Path | str) -> Path:
        """Resolve relative paths as relative to the module in which the subclass is defined."""
        path = Path(path)

        if path.is_absolute():
            return path

        # TODO: Should we use the cwd instead of the module path? Then it would work for non-proper-subclasses..
        module_path = sys.modules[cls.__module__].__file__
        if module_path == __file__:
            raise ValueError(f'You should only call this method from a proper subclass of `{cls.__name__}`')

        assert module_path is not None, 'Module must be a file-based module'
        root = Path(module_path).parent
        return root / path


def _ensure_yaml_language_server_line(content: str, schema_ref: str) -> str:
    first_line = content.split('\n', 1)[0]
    yaml_language_server_line = f'# yaml-language-server: $schema={schema_ref}'
    if first_line == yaml_language_server_line:
        return content
    elif first_line.startswith('# yaml-language-server: $schema='):
        return '\n'.join([yaml_language_server_line] + content.split('\n')[1:])
    else:
        return f'{yaml_language_server_line}\n{content}'


# async def _generate_examples(
#     dataset_type: type[Dataset[Any, Any, Any]],
#     path: Path,
#     model: models.Model | models.KnownModelName = 'gpt-4o',
#     n_examples: int = 3,
# ) -> list[SerializedDatasetRow[Any, Any, Any]]:
#     if path.exists():
#         cases_text = path.read_text()
#         try:
#             loaded = yaml.safe_load(cases_text)
#         except yaml.YAMLError:
#             raise ValueError(f'Cases file {path} is not valid YAML')
#
#         try:
#             existing_cases = dataset_type.type_adapter().validate_python(loaded).rows
#         except ValidationError as e:
#             raise ValueError(
#                 f'Cases file {path} contains data that does not match the schema. Delete the file before calling this function.'
#             ) from e
#     else:
#         existing_cases = []
#
#     n_examples = max(0, n_examples - len(existing_cases))
#     if n_examples == 0:
#         return []
#
#     from pydantic_ai import Agent  # import locally to prevent circular dependencies
#
#     agent = Agent(
#         model,
#         system_prompt=dedent('Generate concise example test cases that comply with the provided JSON schema.'),
#         result_type=dataset_type,
#         retries=1,
#     )
#     return (await agent.run(f'Generate {n_examples} examples')).data.rows


def _get_relative_path_reference(target: Path, source: Path, _prefix: str = '') -> Path:
    # Recursively resolve a relative path to target from source, adding '..' as needed.
    # This is useful for creating a relative path reference from a source file to a target file.
    # For example, if source is '/a/b/c.py' and target is '/a/d/e.py', the relative path reference
    # would be '../../d/e.py'.
    if not target.is_absolute():
        target = target.resolve()
    try:
        return Path(f'{_prefix}{Path(target).relative_to(source)}')
    except ValueError:
        return _get_relative_path_reference(target, source.parent, _prefix=f'{_prefix}../')

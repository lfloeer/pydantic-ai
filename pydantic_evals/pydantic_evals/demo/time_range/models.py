from __future__ import annotations as _annotations

from functools import partial
from typing import Any

from pydantic import AwareDatetime, BaseModel
from typing_extensions import TypedDict

from pydantic_evals.dataset import Dataset
from pydantic_evals.evaluators.common import is_instance, llm_judge
from pydantic_evals.evaluators.spec import Evaluator


class TimeRangeBuilderSuccess(BaseModel, use_attribute_docstrings=True):
    """Response when a time range could be successfully generated."""

    min_timestamp_with_offset: AwareDatetime
    """A datetime in ISO format with timezone offset."""

    max_timestamp_with_offset: AwareDatetime
    """A datetime in ISO format with timezone offset."""

    explanation: str | None
    """
    A brief explanation of the time range that was selected.

    For example, if a user only mentions a specific point in time, you might explain that you selected a 10 minute
    window around that time.
    """

    def __str__(self):
        readable_min_timestamp = self.min_timestamp_with_offset.strftime('%A, %B %d, %Y %H:%M:%S %Z')
        readable_max_timestamp = self.max_timestamp_with_offset.strftime('%A, %B %d, %Y %H:%M:%S %Z')
        lines = [
            'TimeRangeBuilderSuccess:',
            f'* min_timestamp_with_offset: {readable_min_timestamp}',
            f'* max_timestamp_with_offset: {readable_max_timestamp}',
        ]
        if self.explanation is not None:
            lines.append(f'* explanation: {self.explanation}')
        return '\n'.join(lines)


class TimeRangeBuilderError(BaseModel):
    """Response when a time range cannot not be generated."""

    error_message: str

    def __str__(self):
        return f'TimeRangeBuilderError:\n* {self.error_message}'


TimeRangeResponse = TimeRangeBuilderSuccess | TimeRangeBuilderError


class TimeRangeInputs(TypedDict):
    """The inputs for the time range inference agent."""

    prompt: str
    now: AwareDatetime


# TODO(DavidM): Drop the MetadataT type parameter and use the default below once pydantic 2.11 is in use
class TimeRangeDataset(Dataset[TimeRangeInputs, TimeRangeResponse, dict[str, Any]]):
    """A dataset of examples for the time range inference agent."""

    pass


if __name__ == '__main__':

    def main():
        """Usage example."""
        from pathlib import Path

        path = Path(__file__).parent / 'test_cases.yaml'
        if not path.exists():
            evaluator = Evaluator.from_function(partial(llm_judge, rubric='abc'))
            TimeRangeDataset(cases=[], evaluators=[evaluator]).to_file(path, custom_evaluators=[llm_judge, is_instance])

    main()

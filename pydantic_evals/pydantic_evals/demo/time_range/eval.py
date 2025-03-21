import logfire

from pydantic_evals.assessments.common import is_instance, llm_rubric
from pydantic_evals.assessments.llm_as_a_judge import GradingOutput, judge_input_output
from pydantic_evals.dataset import ScoringContext
from pydantic_evals.demo.time_range import TimeRangeResponse, infer_time_range
from pydantic_evals.demo.time_range.models import TimeRangeDataset, TimeRangeInputs


async def judge_time_range_case(inputs: TimeRangeInputs, output: TimeRangeResponse) -> GradingOutput:
    """Judge the output of a time range inference agent based on a rubric."""
    rubric = (
        'The output should be a reasonable time range to select for the given inputs, or an error '
        'message if no good time range could be selected. Pick a score between 0 and 1 to represent how confident '
        'you are that the selected time range was what the user intended, or that an error message was '
        'an appropriate response.'
    )
    return await judge_input_output(inputs, output, rubric)


async def main():
    """TODO: Task: Convert this pydantic_evals.demo package into docs."""
    logfire.configure(
        token='pylf_v1_local_2PPcXqKVdwh4tSLRKtd9797jb8fc0vVZHbTh8V3thPh7',
        send_to_logfire='if-token-present',
        console=logfire.ConsoleOptions(verbose=True),
        advanced=logfire.AdvancedOptions(base_url='http://localhost:8000'),
    )
    dataset = TimeRangeDataset.from_yaml(scorers=[is_instance, llm_rubric])

    async def assess_case(ctx: ScoringContext[TimeRangeInputs, TimeRangeResponse]):
        result = await judge_time_range_case(inputs=ctx.inputs, output=ctx.output)
        return {
            'is_reasonable': 'yes' if result.pass_ else 'no',
            'accuracy': result.score,
        }

    dataset.add_assessment(assess_case)
    report = await dataset.evaluate(infer_time_range, max_concurrency=10)
    report.print(include_input=True, include_output=True)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())

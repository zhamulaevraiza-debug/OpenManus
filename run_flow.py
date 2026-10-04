import asyncio
import time

from app.agent.registry import available_agents
from app.flow.flow_factory import FlowFactory, FlowType
from app.logger import logger


FLOW_TIMEOUT_SECONDS = 3600


async def run_flow():
    # Every available team agent; each plan step runs on a fresh agent of its type
    agents = {spec.key: spec for spec in available_agents(team_only=True)}
    try:
        prompt = input("Enter your prompt: ")

        if not prompt.strip():
            logger.warning("Empty prompt provided.")
            return

        flow = FlowFactory.create_flow(
            flow_type=FlowType.PLANNING,
            agents=agents,
        )
        logger.warning("Processing your request...")

        try:
            start_time = time.time()
            # The final answer is streamed to the terminal while it is generated
            async with asyncio.timeout(FLOW_TIMEOUT_SECONDS):
                await flow.execute(prompt)
            elapsed_time = time.time() - start_time
            logger.info(f"Request processed in {elapsed_time:.2f} seconds")
        except TimeoutError:
            logger.error("Request processing timed out after 1 hour")
            logger.info(
                "Operation terminated due to timeout. Please try a simpler request."
            )

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user.")
    except Exception as e:
        logger.error(f"Error: {str(e)}")


if __name__ == "__main__":
    asyncio.run(run_flow())

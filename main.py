import argparse
import asyncio

from app.agent.registry import create_agent, dispose_agent
from app.logger import logger


async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run Manus agent with a prompt")
    parser.add_argument(
        "--prompt", type=str, required=False, help="Input prompt for the agent"
    )
    args = parser.parse_args()

    # Create and initialize Manus agent
    agent = await create_agent("manus")
    try:
        # Use command line prompt if provided, otherwise ask for input
        prompt = args.prompt if args.prompt else input("Enter your prompt: ")
        if not prompt.strip():
            logger.warning("Empty prompt provided.")
            return

        logger.warning("Processing your request...")
        await agent.run(prompt)
        logger.info("Request processing completed.")
    except KeyboardInterrupt:
        logger.warning("Operation interrupted.")
    except Exception as e:
        logger.error(f"Request failed: {e}")
    finally:
        # Ensure agent resources are cleaned up before exiting
        await dispose_agent(agent)


def cli() -> None:
    """Console script entry point (``openmanus``)."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()

from examples.rentagent_vn.context import RentAgentContext
from examples.rentagent_vn.prompts import SYSTEM_PROMPT
from examples.rentagent_vn.runners import (
    BackgroundResearchRunner,
    BackgroundScrapeRunner,
)
from examples.rentagent_vn.runners.callbacks import (
    progress_callback,
    research_error_callback,
    research_progress_callback,
    research_result_callback,
    research_streaming_url_callback,
    result_callback,
    streaming_url_callback,
    url_complete_callback,
)
from examples.rentagent_vn.tinyfish.client import TinyFishClient
from examples.rentagent_vn.tools import (
    contact_landlord,
    extract_rental_criteria,
    research_area,
    search_rentals,
)
from langclaw import Langclaw

app = Langclaw(system_prompt=SYSTEM_PROMPT, context_schema=RentAgentContext)
app.register_tools(
    [
        search_rentals,
        contact_landlord,
        research_area,
        extract_rental_criteria,
    ]
)
app.role(
    "user",
    tools=[
        "search_rentals",
        "contact_landlord",
        "research_area",
        "extract_rental_criteria",
    ],
)

ONBOARD_AGENT_PROMPT = """\
You are an onboarding assistant helping users set up their rental search called Sarah.

Your job is to have a brief conversation to understand what the user is
looking for, then call the extract_rental_criteria tool with the extracted
parameters. The frontend will automatically navigate to the next step.

Guidelines:
- Be conversational and friendly
- Ask follow-up questions to gather key details: location, budget, bedrooms
- Once you have at least location OR budget, call extract_rental_criteria
- Do NOT repeat what you extracted - the frontend handles the confirmation

Example conversation:
  User: "Hi"
  Assistant: "Hey! Looking for a place to rent? Tell me what you have in mind
             - area, budget, number of bedrooms, or any must-haves."
  User: "Something in District 7, around 10-15 million"
  Assistant: "Got it, District 7 with a budget of 10-15M. How many bedrooms?"
  User: "2 bedrooms, and I need a balcony"
  -> Call extract_rental_criteria tool with the following parameters: (
       district="District 7",
       min_price=10000000,
       max_price=15000000,
       bedrooms=2,
       notes="needs balcony"
     )
"""

app.agent(
    name="onboard_agent",
    description=(
        "Onboarding agent that extracts rental needs from natural language "
        "into structured criteria for the search form."
    ),
    system_prompt=ONBOARD_AGENT_PROMPT,
    tools=[extract_rental_criteria],
)

tinyfish_client = TinyFishClient(timeout=5000)


@app.on_startup
async def _open_tinyfish() -> None:
    await tinyfish_client.open()


@app.on_shutdown
async def _close_tinyfish() -> None:
    await tinyfish_client.close()


scrape_runner = BackgroundScrapeRunner(
    app=app,
    result_callback=result_callback,
    streaming_url_callback=streaming_url_callback,
    progress_callback=progress_callback,
    url_complete_callback=url_complete_callback,
    tinyfish_client=tinyfish_client,
)

research_runner = BackgroundResearchRunner(
    app=app,
    tinyfish_client=tinyfish_client,
    progress_callback=research_progress_callback,
    result_callback=research_result_callback,
    error_callback=research_error_callback,
    streaming_url_callback=research_streaming_url_callback,
)

app.set_context_defaults(
    scrape_runner=scrape_runner,
    research_runner=research_runner,
    rental_urls=["https://www.facebook.com/groups/1930421007111976/"],
)

if __name__ == "__main__":
    app.run()

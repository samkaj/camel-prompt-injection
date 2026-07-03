"""Example tools + policies for run_camel.py.

Run:
    uv run --env-file .env run_camel.py \
        --tools examples/custom_camel_tools.py \
        --model google:gemini-2.5-flash \
        "find me a mug and message it to alice@example.com"
"""

from camel.capabilities import is_trusted
from camel.security_policy import Allowed, Denied


# --- Tools -----------------------------------------------------------------
# Each tool needs a short docstring plus `:param name: ...` lines and full
# type hints -- AgentDojo's `make_function` uses them to build the schema
# shown to the privileged LLM.


def search_products(query: str) -> list[dict]:
    """Search the product catalog.

    :param query: Search terms (case-insensitive substring match).
    """
    catalog = [
        {"name": "Blue Sparrow T-shirt", "price": 25.0},
        {"name": "Blue Sparrow Mug", "price": 12.0},
        {"name": "Blue Sparrow Notebook", "price": 8.0},
    ]
    return [p for p in catalog if query.lower() in p["name"].lower()]


def get_user_email() -> str:
    """Return the current user's email address."""
    return "emma@bluesparrowtech.com"


def send_message(recipient: str, body: str) -> str:
    """Send a direct message.

    :param recipient: Email address of the recipient.
    :param body: Message content.
    """
    return f"Sent to {recipient}: {body!r}"


TOOLS = [search_products, get_user_email, send_message]


# --- Policies --------------------------------------------------------------

NO_SIDE_EFFECT_TOOLS = {"search_products", "get_user_email"}


def send_message_policy(tool_name, kwargs):
    """Only send messages whose recipient came from a trusted source."""
    if not is_trusted(kwargs["recipient"]):
        return Denied("recipient must originate from a trusted source (user input or a trusted tool)")
    return Allowed()


POLICIES = [
    ("send_message", send_message_policy),
    # Catch-all: allow anything the more specific policies didn't cover.
    # Without this, the base engine denies unmatched tools by default.
    ("*", lambda tool_name, kwargs: Allowed()),
]

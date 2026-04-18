import streamlit as st
from groq import Groq
from properties import PROPERTIES, format_properties_for_prompt
import os, re
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="🏠 PropBot – Real Estate Assistant",
    page_icon="🏠",
    layout="centered",
)

st.markdown("""
<style>
    .stChatMessage { border-radius: 12px; }
    .main { max-width: 780px; }
    div[data-testid="stChatInput"] textarea { font-size: 15px; }
</style>
""", unsafe_allow_html=True)

# ── Price parser → always returns value in Lakhs ──────────────
def price_to_lakhs(price_str):
    """Convert price string like '₹1.5 Crore' or '₹85 Lakh' to float in Lakhs."""
    if not price_str or price_str == "Price on request":
        return float("inf")
    s = str(price_str).replace(",", "").replace("₹", "").strip()
    cr  = re.search(r"([\d.]+)\s*Crore", s, re.IGNORECASE)
    lac = re.search(r"([\d.]+)\s*Lakh", s, re.IGNORECASE)
    if cr:  return float(cr.group(1)) * 100
    if lac: return float(lac.group(1))
    # plain number fallback
    num = re.search(r"[\d.]+", s)
    return float(num.group()) if num else float("inf")

# ── Build a pre-sorted, pre-filtered property block for prompt ─
def build_context(city=None, sort_by_price=None, max_price_lakh=None, min_price_lakh=None, bhk=None):
    props = PROPERTIES

    if city:
        props = [p for p in props if p["city"].lower() == city.lower()]
    if bhk:
        props = [p for p in props if str(bhk) in str(p.get("bhk", ""))]
    if max_price_lakh:
        props = [p for p in props if price_to_lakhs(p["price"]) <= max_price_lakh]
    if min_price_lakh:
        props = [p for p in props if price_to_lakhs(p["price"]) >= min_price_lakh]
    if sort_by_price == "asc":
        props = sorted(props, key=lambda p: price_to_lakhs(p["price"]))
    elif sort_by_price == "desc":
        props = sorted(props, key=lambda p: price_to_lakhs(p["price"]), reverse=True)

    if not props:
        return "No properties found matching the criteria."

    lines = ["name | city | locality | type | bhk | area | price | furnish | status"]
    for p in props:
        lines.append(
            f"{p['name']} | {p['city']} | {p['locality']} | {p['type']} | "
            f"{p['bhk']} | {p['area']} | {p['price']} | {p['furnish']} | {p['status']}"
        )
    return "\n".join(lines)

# ── Detect sorting / filtering intent from user message ───────
def detect_intent(message):
    msg = message.lower()
    intent = {"city": None, "sort_by_price": None, "max_price_lakh": None, "min_price_lakh": None, "bhk": None}

    # City detection
    for city in ["gurgaon", "mumbai", "hyderabad", "kolkata"]:
        if city in msg:
            intent["city"] = city.capitalize()
            break

    # Sort direction
    if any(w in msg for w in ["ascending", "lowest", "cheapest", "low to high", "asc"]):
        intent["sort_by_price"] = "asc"
    elif any(w in msg for w in ["descending", "highest", "expensive", "high to low", "desc"]):
        intent["sort_by_price"] = "desc"
    elif "sort" in msg and "price" in msg:
        intent["sort_by_price"] = "asc"  # default sort = ascending

    # BHK detection
    bhk_match = re.search(r"(\d)\s*bhk", msg)
    if bhk_match:
        intent["bhk"] = bhk_match.group(1)

    # Budget detection — "under X crore / lakh"
    under_cr  = re.search(r"under\s+([\d.]+)\s*crore", msg)
    under_lac = re.search(r"under\s+([\d.]+)\s*lakh", msg)
    above_cr  = re.search(r"above\s+([\d.]+)\s*crore", msg)
    above_lac = re.search(r"above\s+([\d.]+)\s*lakh", msg)
    if under_cr:  intent["max_price_lakh"] = float(under_cr.group(1)) * 100
    if under_lac: intent["max_price_lakh"] = float(under_lac.group(1))
    if above_cr:  intent["min_price_lakh"] = float(above_cr.group(1)) * 100
    if above_lac: intent["min_price_lakh"] = float(above_lac.group(1))

    return intent

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.title("🏠 PropBot")
    st.caption("Your AI real estate assistant")
    st.divider()

    api_key = st.text_input(
        "Groq API Key",
        type="password",
        placeholder="gsk_...",
        help="Get a FREE key at console.groq.com",
        value=os.getenv("GROQ_API_KEY", ""),
    )

    st.divider()
    st.markdown("**Available properties**")
    for p in PROPERTIES:
        st.markdown(f"- {p['name']} ({p['city']}) — {p['price']}")

    st.divider()
    if st.button("🗑️ Clear chat"):
        st.session_state.messages = []
        st.rerun()

# ── Base system prompt ────────────────────────────────────────
BASE_SYSTEM_PROMPT = """
You are PropBot, a friendly and knowledgeable real estate assistant.
Answer questions about properties using ONLY the property list provided to you.

Guidelines:
- Be warm, concise, and helpful.
- Present properties in the EXACT ORDER they appear in the list — do not re-sort.
- Show prices exactly as listed (Crore / Lakh).
- If a user seems interested, suggest they book a site visit.
- Never make up details not in the list.
- Answer in a clean numbered list when showing multiple properties.
"""

# ── Chat state ────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

# ── Header ────────────────────────────────────────────────────
st.title("🏠 PropBot")
st.caption("Ask me anything — pricing, location, sorting & more!")
st.divider()

# ── Render chat history ───────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# ── Handle new message ────────────────────────────────────────
if prompt := st.chat_input("e.g. Show 3BHK in Gurgaon sorted by price low to high"):

    if not api_key:
        st.error("Please enter your Groq API key in the sidebar.")
        st.info("Get a free key at: https://console.groq.com")
        st.stop()

    with st.chat_message("user"):
        st.write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    try:
        # Detect intent and build pre-processed context
        intent  = detect_intent(prompt)
        context = build_context(**intent)

        # Build system prompt with the relevant (pre-sorted) property data
        system_prompt = BASE_SYSTEM_PROMPT + f"\n\nHere are the relevant properties (already sorted/filtered for you):\n\n{context}"

        client = Groq(api_key=api_key)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *st.session_state.messages,
                    ],
                    temperature=0.5,
                    max_tokens=600,
                )
                reply = response.choices[0].message.content
            st.write(reply)

        st.session_state.messages.append({"role": "assistant", "content": reply})

    except Exception as e:
        err = str(e)
        if "401" in err:
            st.error("Invalid API key. Check your Groq key at console.groq.com")
        elif "429" in err:
            st.error("Rate limit hit. Wait a few seconds and try again.")
        else:
            st.error(f"API error: {e}")

import streamlit as st
from openai import OpenAI
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

# ── Price parser → float in Lakhs ─────────────────────────────
def price_to_lakhs(price_str):
    if not price_str or price_str == "Price on request":
        return float("inf")
    s = str(price_str).replace(",", "").replace("₹", "").strip()
    cr  = re.search(r"([\d.]+)\s*Crore", s, re.IGNORECASE)
    lac = re.search(r"([\d.]+)\s*Lakh",  s, re.IGNORECASE)
    if cr:  return float(cr.group(1)) * 100
    if lac: return float(lac.group(1))
    num = re.search(r"[\d.]+", s)
    return float(num.group()) if num else float("inf")

# ── Filter + sort properties in Python (never trust LLM for this)
def get_filtered_props(city=None, sort_by_price=None, max_price_lakh=None,
                       min_price_lakh=None, bhk=None):
    props = list(PROPERTIES)
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
    return props

# ── Detect intent from user message ───────────────────────────
def detect_intent(message):
    msg = message.lower()
    intent = {"city": None, "sort_by_price": None,
              "max_price_lakh": None, "min_price_lakh": None, "bhk": None}

    for city in ["gurgaon", "mumbai", "hyderabad", "kolkata"]:
        if city in msg:
            intent["city"] = city.capitalize()
            break

    if any(w in msg for w in ["ascending", "lowest", "cheapest", "low to high", "asc", "ascending order"]):
        intent["sort_by_price"] = "asc"
    elif any(w in msg for w in ["descending", "highest", "expensive", "high to low", "desc"]):
        intent["sort_by_price"] = "desc"
    elif "sort" in msg and "price" in msg:
        intent["sort_by_price"] = "asc"

    bhk_match = re.search(r"(\d)\s*bhk", msg)
    if bhk_match:
        intent["bhk"] = bhk_match.group(1)

    under_cr  = re.search(r"under\s+([\d.]+)\s*crore", msg)
    under_lac = re.search(r"under\s+([\d.]+)\s*lakh",  msg)
    above_cr  = re.search(r"above\s+([\d.]+)\s*crore", msg)
    above_lac = re.search(r"above\s+([\d.]+)\s*lakh",  msg)
    if under_cr:  intent["max_price_lakh"] = float(under_cr.group(1)) * 100
    if under_lac: intent["max_price_lakh"] = float(under_lac.group(1))
    if above_cr:  intent["min_price_lakh"] = float(above_cr.group(1)) * 100
    if above_lac: intent["min_price_lakh"] = float(above_lac.group(1))

    return intent

# ── Render property cards directly in Streamlit (no LLM) ──────
def render_property_cards(props):
    for i, p in enumerate(props, 1):
        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{i}. {p['name']}**")
                st.caption(f"📍 {p['locality']}, {p['city']}  •  {p['type']}  •  {p['bhk']}  •  {p['area']}")
                st.caption(f"🛋️ {p['furnish']}  •  {p['status']}")
            with col2:
                st.markdown(f"### {p['price']}")
            st.divider()

# ── Check if message is a property search query ───────────────
def is_search_query(message):
    msg = message.lower()
    search_keywords = [
        "suggest", "show", "list", "find", "properties", "flats",
        "apartments", "available", "sorted", "sort", "under", "above",
        "cheapest", "expensive", "bhk", "budget"
    ]
    return any(w in msg for w in search_keywords)

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.title("🏠 PropBot")
    st.caption("Your AI real estate assistant")
    st.divider()

    # API key loaded silently from Streamlit secrets / .env — never shown in UI
    api_key = os.getenv("CEREBRAS_API_KEY", "")

    st.divider()
    st.markdown("**Cities available**")
    for city in ["Gurgaon", "Mumbai", "Hyderabad", "Kolkata"]:
        count = sum(1 for p in PROPERTIES if p["city"] == city)
        st.markdown(f"- {city} — {count} properties")

    st.divider()
    if st.button("🗑️ Clear chat"):
        st.session_state.messages = []
        st.rerun()

# ── System prompt (for general Q&A only) ──────────────────────
BASE_SYSTEM_PROMPT = f"""
You are PropBot, a friendly real estate assistant.
Use the property data below to answer general questions.
Do NOT list or sort properties yourself — that is handled by the app.
Just give a short, warm 1-2 sentence intro or answer to the user's question.

Property data (for reference only):
{format_properties_for_prompt()}
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
        if msg.get("cards"):
            st.write(msg["content"])
            render_property_cards(msg["cards"])
        else:
            st.write(msg["content"])

# ── Handle new message ────────────────────────────────────────
if prompt := st.chat_input("e.g. Show 3BHK in Gurgaon sorted by price low to high"):

    if not api_key:
        st.error("This app is not configured yet. Please contact the administrator.")
        st.stop()

    with st.chat_message("user"):
        st.write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.cerebras.ai/v1")

        if is_search_query(prompt):
            # ── Search path: Python sorts, Streamlit renders ──
            intent  = detect_intent(prompt)
            city    = intent.get("city")
            AVAILABLE_CITIES = ["Gurgaon", "Mumbai", "Hyderabad", "Kolkata"]

            # If user asked for a city we don't have — stop immediately
            msg_lower = prompt.lower()
            asked_unknown_city = any(
                w in msg_lower for w in ["in ", "at ", "near "]
            ) and city is None and any(
                c not in msg_lower for c in [c.lower() for c in AVAILABLE_CITIES]
            )

            # Simpler check: detect if a non-available city was mentioned
            known = [c.lower() for c in AVAILABLE_CITIES]
            location_words = [w for w in msg_lower.split() if len(w) > 3
                              and w not in ["show","find","list","some","with",
                                            "price","sort","properties","flats",
                                            "under","above","available","bhk",
                                            "suggest","give","what","best","good"]]
            unknown_city_asked = any(w not in known and w not in
                                     ["".join(c.lower().split()) for c in AVAILABLE_CITIES]
                                     for w in location_words) and city is None and len(location_words) > 0

            with st.chat_message("assistant"):
                if city is None and unknown_city_asked:
                    reply = (f"Sorry, we currently only have properties in "                             f"**Gurgaon, Mumbai, Hyderabad and Kolkata**. "                             f"We don't have any listings for that location yet!")
                    st.write(reply)
                    st.session_state.messages.append({"role": "assistant", "content": reply, "cards": []})
                else:
                    props = get_filtered_props(**intent)
                    if not props:
                        reply = (f"Sorry, no properties found matching your criteria. "                                 f"We have listings in Gurgaon, Mumbai, Hyderabad and Kolkata.")
                        st.write(reply)
                        st.session_state.messages.append({"role": "assistant", "content": reply, "cards": []})
                    else:
                        # Give LLM the actual city/filter context for an accurate intro
                        filter_desc = f"city={city or 'all cities'}, sort={intent.get('sort_by_price') or 'none'}"
                        intro_response = client.chat.completions.create(
                            model="llama-3.3-70b",
                            messages=[
                                {"role": "system", "content": (
                                    f"You are a real estate assistant. We have properties ONLY in Gurgaon, Mumbai, Hyderabad and Kolkata. "
                                    f"Write exactly ONE short sentence introducing {len(props)} results for: {filter_desc}. "
                                    "Do NOT mention any other city. Do not list properties.")},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.3,
                            max_tokens=60,
                        )
                        intro = intro_response.choices[0].message.content.strip()
                        st.write(intro)
                        render_property_cards(props)
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": intro,
                            "cards": props,
                        })
        else:
            # ── General Q&A path: LLM answers normally ────────
            response = client.chat.completions.create(
                model="llama-3.3-70b",
                messages=[
                    {"role": "system", "content": BASE_SYSTEM_PROMPT},
                    *[{"role": m["role"], "content": m["content"]} for m in st.session_state.messages],
                ],
                temperature=0.7,
                max_tokens=400,
            )
            reply = response.choices[0].message.content

            with st.chat_message("assistant"):
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

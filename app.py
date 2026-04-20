import streamlit as st
from groq import Groq
from properties import PROPERTIES, format_properties_for_prompt
import os, re, json
from dotenv import load_dotenv
from rapidfuzz import process

load_dotenv()

st.set_page_config(
    page_title="PropBot – Real Estate Assistant",
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

AVAILABLE_CITIES   = ["Gurgaon", "Mumbai", "Hyderabad", "Kolkata"]
AVAILABLE_FURNISH  = ["Furnished", "Semifurnished", "Unfurnished"]
AVAILABLE_TYPES    = ["Residential Apartment", "Independent House/Villa",
                      "Independent/Builder Floor", "Residential Land"]
AVAILABLE_STATUS   = ["ready to move", "under construction", "resale",
                      "new booking", "rera", "new launch"]

# ── Parsers ────────────────────────────────────────────────────
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

def bhk_to_int(bhk_str):
    try: return int(str(bhk_str).replace("BHK","").strip())
    except: return 99

def area_to_sqft(area_str):
    try:
        nums = re.findall(r"[\d.]+", str(area_str))
        return float(nums[0]) if nums else 0
    except: return 0

# ── Master filter + sort function ─────────────────────────────
def get_filtered_props(
    city=None, bhk=None,
    furnish=None, property_type=None, status_filter=None,
    sort_by_price=None, sort_by_bhk=None, sort_by_area=None,
    max_price_lakh=None, min_price_lakh=None,
    min_area_sqft=None, max_area_sqft=None,
    **kwargs  # absorb unknown keys safely
):
    props = list(PROPERTIES)

    # ── Filters ────────────────────────────────────────────────
    if city:
        props = [p for p in props if p["city"].lower() == city.lower()]

    if bhk:
        props = [p for p in props if str(bhk) in str(p.get("bhk", ""))]

    if furnish:
        props = [p for p in props
                 if p.get("furnish","").lower() == furnish.lower()]

    if property_type:
        props = [p for p in props
                 if property_type.lower() in p.get("type","").lower()]

    if status_filter:
        props = [p for p in props
                 if status_filter.lower() in p.get("status","").lower()]

    if max_price_lakh:
        props = [p for p in props if price_to_lakhs(p["price"]) <= max_price_lakh]

    if min_price_lakh:
        props = [p for p in props if price_to_lakhs(p["price"]) >= min_price_lakh]

    if min_area_sqft:
        props = [p for p in props if area_to_sqft(p.get("area","")) >= min_area_sqft]

    if max_area_sqft:
        props = [p for p in props if area_to_sqft(p.get("area","")) <= max_area_sqft]

    # ── Sorts ──────────────────────────────────────────────────
    if sort_by_price == "asc":
        props = sorted(props, key=lambda p: price_to_lakhs(p["price"]))
    elif sort_by_price == "desc":
        props = sorted(props, key=lambda p: price_to_lakhs(p["price"]), reverse=True)

    if sort_by_bhk == "asc":
        props = sorted(props, key=lambda p: bhk_to_int(p.get("bhk","99")))
    elif sort_by_bhk == "desc":
        props = sorted(props, key=lambda p: bhk_to_int(p.get("bhk","99")), reverse=True)

    if sort_by_area == "asc":
        props = sorted(props, key=lambda p: area_to_sqft(p.get("area","")))
    elif sort_by_area == "desc":
        props = sorted(props, key=lambda p: area_to_sqft(p.get("area","")), reverse=True)

    return props

# ── LLM intent extraction ──────────────────────────────────────
def llm_extract_intent(message, client, last_city=None, last_props=None):
    last_city_hint = f"Previous city context: {last_city}." if last_city else ""
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"""Extract property search intent from user message.
{last_city_hint}
If user says "them/those/these/arrange/sort/filter" without a city → use previous city context.

Return ONLY valid JSON with these keys:
{{
  "city": null or one of ["Gurgaon","Mumbai","Hyderabad","Kolkata"],
  "bhk": null or number (1-5),
  "furnish": null or one of ["Furnished","Semifurnished","Unfurnished"],
  "property_type": null or one of ["Residential Apartment","Independent House/Villa","Independent/Builder Floor","Residential Land"],
  "status_filter": null or one of ["ready to move","under construction","resale","new booking","rera"],
  "sort_by_price": null or "asc" or "desc",
  "sort_by_bhk": null or "asc" or "desc",
  "sort_by_area": null or "asc" or "desc",
  "max_price_lakh": null or number (convert crore→lakh: 1cr=100L),
  "min_price_lakh": null or number,
  "min_area_sqft": null or number,
  "max_area_sqft": null or number,
  "is_search": true if user wants to search/find/list/sort/arrange/filter properties,
  "unknown_city": true if user asked for city NOT in the available list,
  "is_followup_qa": true if user asks question about previously shown properties
}}

Handle spelling/Hindi/synonyms:
- "gurgoan/gurugram" → Gurgaon
- "furnished/fully furnished/furnish" → Furnished
- "semi furnished/semifurnish" → Semifurnished
- "unfurnished/bare" → Unfurnished
- "villa/house/bungalow" → Independent House/Villa
- "flat/apartment" → Residential Apartment
- "plot/land" → Residential Land
- "builder floor/floor" → Independent/Builder Floor
- "sasta/cheap/low price" → sort_by_price asc
- "mehnge/expensive/costly" → sort_by_price desc
- "ready/move in" → status ready to move
- "under construction/new" → status under construction
- "big/large/spacious" → sort_by_area desc
- "small/compact/tiny" → sort_by_area asc
- "2 room/2 bedroom/2 bhk" → bhk 2
- "arrange by size/area" → sort_by_area asc
- "arrange by rooms/bhk/no of rooms/number of rooms" → sort_by_bhk asc, bhk must be null (do NOT filter by bhk when sorting by bhk)

Return ONLY JSON."""},
                {"role": "user", "content": message},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except Exception:
        return None

# ── Fuzzy fallback ─────────────────────────────────────────────
def fuzzy_extract_intent(message, last_city=None):
    msg = message.lower()
    intent = {
        "city": None, "bhk": None, "furnish": None,
        "property_type": None, "status_filter": None,
        "sort_by_price": None, "sort_by_bhk": None, "sort_by_area": None,
        "max_price_lakh": None, "min_price_lakh": None,
        "min_area_sqft": None, "max_area_sqft": None,
        "is_search": False, "unknown_city": False, "is_followup_qa": False,
    }

    # City
    for word in msg.split():
        if len(word) < 3: continue
        match, score, _ = process.extractOne(word, [c.lower() for c in AVAILABLE_CITIES])
        if score >= 75:
            intent["city"] = AVAILABLE_CITIES[[c.lower() for c in AVAILABLE_CITIES].index(match)]
            break

    # Inherit last city for follow-ups
    if not intent["city"] and last_city:
        if any(w in msg for w in ["them","those","these","arrange","sort","filter","show","it","their"]):
            intent["city"] = last_city

    # Furnish
    if any(w in msg for w in ["fully furnished","fully-furnished","furnished"]) and "semi" not in msg and "un" not in msg:
        intent["furnish"] = "Furnished"
    elif any(w in msg for w in ["semifurnished","semi furnished","semi-furnished","semi"]):
        intent["furnish"] = "Semifurnished"
    elif any(w in msg for w in ["unfurnished","un furnished","bare","empty"]):
        intent["furnish"] = "Unfurnished"

    # Property type
    if any(w in msg for w in ["villa","bungalow","house","independent house"]):
        intent["property_type"] = "Independent House/Villa"
    elif any(w in msg for w in ["plot","land"]):
        intent["property_type"] = "Residential Land"
    elif any(w in msg for w in ["builder floor","floor"]):
        intent["property_type"] = "Independent/Builder Floor"
    elif any(w in msg for w in ["flat","apartment","flats","apartments"]):
        intent["property_type"] = "Residential Apartment"

    # Status
    if any(w in msg for w in ["ready to move","ready","move in","movein"]):
        intent["status_filter"] = "ready to move"
    elif any(w in msg for w in ["under construction","underconstruction","new project","new launch"]):
        intent["status_filter"] = "under construction"
    elif "resale" in msg:
        intent["status_filter"] = "resale"
    elif any(w in msg for w in ["new booking","book now"]):
        intent["status_filter"] = "new booking"
    elif "rera" in msg:
        intent["status_filter"] = "rera"

    # Sorts
    if any(w in msg for w in ["ascending","cheapest","low to high","sasta","saste","affordable","cheap"]):
        intent["sort_by_price"] = "asc"
    elif any(w in msg for w in ["descending","expensive","high to low","mehnge","costly","luxury","premium"]):
        intent["sort_by_price"] = "desc"

    if "arrange" in msg and any(w in msg for w in ["room","rooms","bhk","bedroom"]):
        intent["sort_by_bhk"] = "asc"

    if any(w in msg for w in ["biggest","largest","spacious","big area","large area"]):
        intent["sort_by_area"] = "desc"
    elif any(w in msg for w in ["smallest","compact","small area","tiny"]):
        intent["sort_by_area"] = "asc"

    # BHK
    bhk_match = re.search(r"(\d)\s*(?:bhk|room|bedroom|br\b)", msg)
    if bhk_match:
        intent["bhk"] = bhk_match.group(1)

    # Price
    under_cr  = re.search(r"under\s+([\d.]+)\s*(?:crore|cr)", msg)
    under_lac = re.search(r"under\s+([\d.]+)\s*(?:lakh|lac|l\b)", msg)
    above_cr  = re.search(r"above\s+([\d.]+)\s*(?:crore|cr)", msg)
    above_lac = re.search(r"above\s+([\d.]+)\s*(?:lakh|lac|l\b)", msg)
    budget_cr  = re.search(r"budget\s+(?:of\s+)?([\d.]+)\s*(?:crore|cr)", msg)
    budget_lac = re.search(r"budget\s+(?:of\s+)?([\d.]+)\s*(?:lakh|lac)", msg)
    if under_cr:   intent["max_price_lakh"] = float(under_cr.group(1)) * 100
    if under_lac:  intent["max_price_lakh"] = float(under_lac.group(1))
    if above_cr:   intent["min_price_lakh"] = float(above_cr.group(1)) * 100
    if above_lac:  intent["min_price_lakh"] = float(above_lac.group(1))
    if budget_cr:  intent["max_price_lakh"] = float(budget_cr.group(1)) * 100
    if budget_lac: intent["max_price_lakh"] = float(budget_lac.group(1))

    # Area sqft
    min_area = re.search(r"(?:above|more than|min|minimum|atleast)\s+([\d.]+)\s*(?:sqft|sq\.ft|sq ft)", msg)
    max_area = re.search(r"(?:under|less than|max|maximum|below|upto)\s+([\d.]+)\s*(?:sqft|sq\.ft|sq ft)", msg)
    if min_area: intent["min_area_sqft"] = float(min_area.group(1))
    if max_area: intent["max_area_sqft"] = float(max_area.group(1))

    # is_search
    search_words = [
        "suggest","show","list","find","properties","flats","flat","apartments",
        "apartment","available","sorted","sort","under","above","cheapest",
        "expensive","bhk","budget","dikhao","batao","property","ghar","makaan",
        "homes","home","room","rooms","cheap","affordable","luxury","search",
        "looking","arrange","filter","sasta","mehnge","villa","plot","land",
        "furnished","semifurnished","unfurnished","ready","construction"
    ]
    intent["is_search"] = any(w in msg for w in search_words)

    # is_followup_qa
    qa_words = ["special","best","compare","difference","which","what is",
                "tell me about","expensive and cheap","highlight","feature",
                "amenities","about them","about these"]
    intent["is_followup_qa"] = any(w in msg for w in qa_words)

    return intent

# ── Option C: LLM first, fuzzy fallback ───────────────────────
def detect_intent(message, client, last_city=None, last_props=None):
    intent = llm_extract_intent(message, client, last_city, last_props)
    if intent is None:
        intent = fuzzy_extract_intent(message, last_city)
    else:
        # Validate + fix city
        if intent.get("city") and intent["city"] not in AVAILABLE_CITIES:
            match, score, _ = process.extractOne(intent["city"], AVAILABLE_CITIES)
            intent["city"] = match if score >= 75 else None
        # Inherit last city for follow-ups
        if not intent.get("city") and last_city:
            if any(w in message.lower() for w in ["them","those","these","arrange","sort","filter","their"]):
                intent["city"] = last_city
    # Safety: if user wants to SORT by bhk, don't FILTER by bhk too
    if intent.get('sort_by_bhk') and intent.get('bhk'):
        intent['bhk'] = None

    return intent

# ── Render property cards (with optional BHK grouping) ────────
def render_property_cards(props, group_by_bhk=False):
    if group_by_bhk:
        # Group properties by BHK and show category headers
        from collections import defaultdict
        groups = defaultdict(list)
        for p in props:
            groups[p.get("bhk", "N/A")].append(p)

        # Sort group keys: 1BHK, 2BHK, 3BHK... then N/A last
        def bhk_sort_key(k):
            try: return int(str(k).replace("BHK","").strip())
            except: return 99

        counter = 1
        for bhk_key in sorted(groups.keys(), key=bhk_sort_key):
            bhk_props = groups[bhk_key]
            st.markdown(f"#### 🏠 {bhk_key} — {len(bhk_props)} {'property' if len(bhk_props)==1 else 'properties'}")
            for p in bhk_props:
                with st.container():
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**{counter}. {p['name']}**")
                        st.caption(f"📍 {p['locality']}, {p['city']}  •  {p['type']}  •  {p['area']}")
                        st.caption(f"🛋️ {p['furnish']}  •  {p['status']}")
                    with col2:
                        st.markdown(f"### {p['price']}")
                    st.divider()
                counter += 1
    else:
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

# ── Format props as text for LLM Q&A context ──────────────────
def props_to_text(props):
    lines = []
    for i, p in enumerate(props, 1):
        lines.append(
            f"{i}. {p['name']} | {p['city']} | {p['bhk']} | "
            f"{p['area']} | {p['price']} | {p['furnish']} | "
            f"{p['type']} | {p['status']}"
        )
    return "\n".join(lines)

# ── Build human-readable filter summary ───────────────────────
def filter_summary(intent, num_results):
    parts = []
    if intent.get("city"):          parts.append(intent["city"])
    if intent.get("bhk"):           parts.append(f"{intent['bhk']}BHK")
    if intent.get("furnish"):       parts.append(intent["furnish"])
    if intent.get("property_type"): parts.append(intent["property_type"])
    if intent.get("status_filter"): parts.append(intent["status_filter"].title())
    if intent.get("max_price_lakh"):parts.append(f"under ₹{intent['max_price_lakh']}L")
    if intent.get("min_price_lakh"):parts.append(f"above ₹{intent['min_price_lakh']}L")
    if intent.get("min_area_sqft"): parts.append(f"min {intent['min_area_sqft']} sqft")
    if intent.get("max_area_sqft"): parts.append(f"max {intent['max_area_sqft']} sqft")
    sorts = []
    if intent.get("sort_by_price"): sorts.append(f"price {'↑' if intent['sort_by_price']=='asc' else '↓'}")
    if intent.get("sort_by_bhk"):   sorts.append(f"BHK {'↑' if intent['sort_by_bhk']=='asc' else '↓'}")
    if intent.get("sort_by_area"):  sorts.append(f"area {'↑' if intent['sort_by_area']=='asc' else '↓'}")
    summary = " • ".join(parts) if parts else "All cities"
    if sorts: summary += f"  |  Sorted by: {', '.join(sorts)}"
    summary += f"  |  {num_results} results"
    return summary

# ── Sidebar ───────────────────────────────────────────────────
with st.sidebar:
    st.title("🏠 PropBot")
    st.caption("Your AI real estate assistant")
    st.divider()

    api_key = os.getenv("GROQ_API_KEY", "")

    st.divider()
    st.markdown("**Cities available**")
    for city in AVAILABLE_CITIES:
        count = sum(1 for p in PROPERTIES if p["city"] == city)
        st.markdown(f"- {city} — {count} properties")
    st.divider()
    st.markdown("**Filters you can use**")
    st.caption("City • BHK • Furnished/Semi/Unfurnished\nVilla/Flat/Plot/Floor • Ready to Move\nUnder Construction • Price • Area sqft\nSort by Price / BHK / Area")
    st.divider()
    if st.button("🗑️ Clear chat"):
        st.session_state.messages   = []
        st.session_state.last_city  = None
        st.session_state.last_props = []
        st.rerun()

# ── System prompt ─────────────────────────────────────────────
BASE_SYSTEM_PROMPT = f"""
You are PropBot, a friendly real estate assistant.
We ONLY have properties in Gurgaon, Mumbai, Hyderabad and Kolkata.
Do NOT list or sort properties yourself — the app handles that.
Give short, warm 1-2 sentence answers only.

Property data (for reference):
{format_properties_for_prompt()}
"""

# ── Session state ─────────────────────────────────────────────
if "messages"   not in st.session_state: st.session_state.messages   = []
if "last_city"  not in st.session_state: st.session_state.last_city  = None
if "last_props" not in st.session_state: st.session_state.last_props = []

# ── Header ────────────────────────────────────────────────────
st.title("🏠 PropBot")
st.caption("Ask me anything — city, BHK, furnished, price, area, status & more!")
st.divider()

# ── Render chat history ───────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("cards") is not None:
            st.write(msg["content"])
            if msg["cards"]:
                render_property_cards(msg["cards"])
        else:
            st.write(msg["content"])

# ── Handle new message ────────────────────────────────────────
if prompt := st.chat_input("e.g. furnished 2BHK flats in Mumbai under 2 crore"):

    if not api_key:
        st.error("This app is not configured yet. Please contact the administrator.")
        st.stop()

    with st.chat_message("user"):
        st.write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    try:
        client = Groq(api_key=api_key)

        intent = detect_intent(
            prompt, client,
            last_city=st.session_state.last_city,
            last_props=st.session_state.last_props,
        )

        is_search      = intent.pop("is_search",      False)
        unknown_city   = intent.pop("unknown_city",   False)
        is_followup_qa = intent.pop("is_followup_qa", False)
        city           = intent.get("city")

        with st.chat_message("assistant"):

            # ── Follow-up Q&A on shown props ──────────────────
            if is_followup_qa and st.session_state.last_props:
                shown_text = props_to_text(st.session_state.last_props)
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": (
                            "You are a real estate assistant. Answer ONLY using "
                            "the properties listed below — do not use any other properties.\n\n"
                            f"Properties shown to user:\n{shown_text}")},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=400,
                )
                reply = response.choices[0].message.content
                st.write(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})

            # ── Property search ────────────────────────────────
            elif is_search:
                if unknown_city and not city:
                    reply = ("Sorry, we currently only have properties in "
                             "**Gurgaon, Mumbai, Hyderabad and Kolkata**. "
                             "We don't have any listings for that location yet!")
                    st.write(reply)
                    st.session_state.messages.append({"role": "assistant", "content": reply, "cards": []})
                else:
                    props = get_filtered_props(**intent)

                    # Save context
                    if city: st.session_state.last_city = city
                    st.session_state.last_props = props

                    summary = filter_summary(intent, len(props))
                    st.caption(f"🔍 {summary}")

                    if not props:
                        furnish_val = intent.get("furnish","")
                        city_val    = city or "all cities"
                        reply = (f"Sorry, no **{furnish_val+' ' if furnish_val else ''}properties** "
                                 f"found in **{city_val}** matching your criteria. "
                                 f"Try removing some filters!")
                        st.write(reply)
                        st.session_state.messages.append({"role": "assistant", "content": reply, "cards": []})
                    else:
                        render_property_cards(props)
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"🔍 {summary}",
                            "cards": props,
                        })

            # ── General Q&A ────────────────────────────────────
            else:
                sys_prompt = BASE_SYSTEM_PROMPT
                if st.session_state.last_props:
                    sys_prompt += (f"\n\nUser was last shown:\n"
                                   f"{props_to_text(st.session_state.last_props)}")
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        *[{"role": m["role"], "content": m["content"]}
                          for m in st.session_state.messages],
                    ],
                    temperature=0.7,
                    max_tokens=400,
                )
                reply = response.choices[0].message.content
                st.write(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})

    except Exception as e:
        err = str(e)
        if "401" in err:
            st.error("Authentication error. Please contact the administrator.")
        elif "429" in err:
            st.error("Rate limit hit. Wait a few seconds and try again.")
        else:
            st.error(f"API error: {e}")

"""Regular expressions and word lists shared by the custom recognizers."""

from ..transform.generalize import HOLIDAYS, MONTHS

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
    "AA": "Armed Forces Americas", "AE": "Armed Forces Europe", "AP": "Armed Forces Pacific",
}

# Places at state level or larger: Safe Harbor allows keeping these.
COUNTRIES = {
    "united states", "united states of america", "usa", "us", "u.s.", "u.s.a.", "america", "canada", "mexico",
    "united kingdom", "uk", "england", "scotland", "wales", "ireland", "france", "germany", "spain", "portugal",
    "italy", "netherlands", "belgium", "switzerland", "austria", "sweden", "norway", "denmark", "finland",
    "poland", "russia", "ukraine", "greece", "turkey", "israel", "egypt", "nigeria", "kenya", "south africa",
    "ethiopia", "ghana", "morocco", "india", "pakistan", "bangladesh", "china", "japan", "south korea", "korea",
    "north korea", "taiwan", "hong kong", "singapore", "vietnam", "thailand", "philippines", "indonesia",
    "malaysia", "australia", "new zealand", "brazil", "argentina", "chile", "colombia", "peru", "venezuela",
    "cuba", "jamaica", "haiti", "dominican republic", "guatemala", "honduras", "el salvador", "nicaragua",
    "costa rica", "panama", "iran", "iraq", "saudi arabia", "united arab emirates", "uae", "qatar", "lebanon",
    "syria", "jordan", "afghanistan", "nepal", "sri lanka", "czech republic", "hungary", "romania", "bulgaria",
    "serbia", "croatia", "iceland", "europe", "asia", "africa", "north america", "south america",
    "latin america", "central america", "middle east", "caribbean", "antarctica", "oceania",
}
LARGE_PLACES = COUNTRIES | {s.lower() for s in US_STATES.values()} | {"the united states", "the us", "the u.s.",
                                                                        "midwest", "new england", "the south",
                                                                        "west coast", "east coast"}

STATE_NAMES = "|".join(sorted(US_STATES.values(), key=len, reverse=True))
STATE_ABBR = "|".join(US_STATES)

STREET_SUFFIXES = (
    "Alley|Aly|Annex|Arcade|Avenue|Ave|Bayou|Beach|Bend|Bluff|Bluffs|Bottom|Boulevard|Blvd|Branch|Bridge|Brook|"
    "Brooks|Burg|Burgs|Bypass|Camp|Canyon|Cape|Causeway|Center|Centers|Circle|Cir|Circles|Cliff|Cliffs|Club|"
    "Common|Commons|Corner|Corners|Course|Court|Ct|Courts|Cove|Cv|Coves|Creek|Crescent|Crest|Crossing|Xing|"
    "Crossroad|Curve|Dale|Dam|Divide|Drive|Dr|Drives|Estate|Estates|Expressway|Expy|Extension|Ext|Extensions|"
    "Fall|Falls|Ferry|Field|Fields|Flat|Flats|Ford|Fords|Forest|Forge|Forges|Fork|Forks|Fort|Freeway|Fwy|Garden|"
    "Gardens|Gateway|Glen|Glens|Green|Greens|Grove|Groves|Harbor|Harbors|Haven|Heights|Hts|Highway|Hwy|Hill|"
    "Hills|Hollow|Inlet|Island|Islands|Isle|Junction|Junctions|Key|Keys|Knoll|Knolls|Lake|Lakes|Land|Landing|"
    "Lane|Ln|Light|Lights|Loaf|Lock|Locks|Lodge|Loop|Mall|Manor|Manors|Meadow|Meadows|Mews|Mill|Mills|Mission|"
    "Motorway|Mount|Mountain|Mountains|Neck|Orchard|Oval|Overpass|Park|Parks|Parkway|Pkwy|Parkways|Pass|Passage|"
    "Path|Pike|Pine|Pines|Place|Pl|Plain|Plains|Plaza|Plz|Point|Pt|Points|Port|Ports|Prairie|Radial|Ramp|Ranch|"
    "Rapid|Rapids|Rest|Ridge|Ridges|River|Road|Rd|Roads|Route|Rte|Row|Rue|Run|Shoal|Shoals|Shore|Shores|Skyway|"
    "Spring|Springs|Spur|Spurs|Square|Sq|Squares|Station|Sta|Stravenue|Stream|Street|St|Streets|Summit|Terrace|"
    "Ter|Throughway|Trace|Track|Trafficway|Trail|Trl|Trailer|Tunnel|Turnpike|Underpass|Union|Unions|Valley|"
    "Valleys|Via|Viaduct|View|Views|Village|Villages|Ville|Vista|Walk|Walks|Wall|Way|Ways|Well|Wells"
)
SECONDARY = r"(?:Apt|Apartment|Suite|Ste|Unit|Fl|Floor|Rm|Room|Bldg|Building|Lot|Space|Trlr|Dept|#)\.?\s*#?\s*[A-Za-z0-9-]{1,8}"

STREET_ADDRESS = (
    rf"(?<![\w-])\d{{1,6}}[A-Za-z]?\s+(?:(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\.?\s+)?"
    rf"(?:(?:[A-Z0-9][A-Za-z0-9'.-]*|of|the|de|la|del)\s+){{0,4}}(?:{STREET_SUFFIXES})\b\.?"
    rf"(?:\s+(?:N|S|E|W|NE|NW|SE|SW)\b\.?)?(?:,?\s+{SECONDARY})?"
)
PO_BOX = r"(?i:\bP\.?\s?O\.?\s*Box|\bPost\s+Office\s+Box)\s+\d{1,7}"
MILITARY_ADDRESS = r"\b(?:(?:PSC|Unit|CMR)\s+\d{2,5},?\s+Box\s+\d{1,5}|US[NS]{1,2}V?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"

CITY_STATE_ZIP = (
    rf"(?P<city>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){{0,3}}),\s*(?P<state>{STATE_ABBR}|{STATE_NAMES})"
    rf"(?:[ \t]+(?P<zip>\d{{5}}(?:-\d{{4}})?))?(?![\w-])"
)
# Town names: "Port Kevinberg", "Lake Charles", "North Platte"; "Clarksville" after "in/at/visiting".
PLACE_PREFIX = (r"\b(?:New|North|South|East|West|Port|Lake|Fort|Ft\.|Mount|Mt\.|Saint|St\.|San|Santa|Los|Las|"
                r"Glen|Grand|Little|Big|Palm|Cape|Point|Upper|Lower)[ \t]+[A-Z][a-z]{2,}"
                r"(?:[ \t]+(?:City|Springs|Falls|Beach|Heights|Hills|Park|Valley|Village|Junction|Center))?\b")
PLACE_SUFFIX = (r"(?i:\b(?:in|at|from|to|near|visiting|outside|around|toward|towards|via|lives|living|moved|"
                r"based|located|resident|residing)[ \t]+(?:in[ \t]+|of[ \t]+)?)"
                r"(?P<value>[A-Z][a-z]+(?:ville|burgh|burg|borough|town|ton|field|mouth|port|view|side|haven|berg|"
                r"chester|shire|stad|bury|ford|land|wood|dale|mont|ridge|brook|crest|furt))\b")

LABELED_ZIP = r"(?i:\b(?:zip|zip ?code|postal(?: ?code)?|post ?code)\b)\s*[:#]?\s*(?P<value>\d{5}(?:-\d{4})?)(?!\d)"
STATE_ZIP = rf"\b(?:{STATE_ABBR})\s+(?P<value>\d{{5}}(?:-\d{{4}})?)(?![\d-])"

WEEKDAY = r"(?:mon|tue|tues|wed|wednes|thu|thur|thurs|fri|sat|satur|sun)(?:day)?\.?"
DATE_PATTERNS = [
    rf"(?i)\b(?:{WEEKDAY},?\s+)?(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?:(?:19|20)\d\d|'\d\d))?(?!\d)",
    rf"(?i)\b(?:{WEEKDAY},?\s+)?\d{{1,2}}(?:st|nd|rd|th)?(?:\s+of)?\s+(?:{MONTHS})\.?,?(?:\s+(?:19|20)\d\d)?\b",
    rf"(?i)\b(?:{MONTHS})\.?,?\s+(?:(?:19|20)\d\d|'\d\d)\b",
    r"(?<![\d/.-])(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:(?:19|20)\d\d|\d\d)(?![\d/-]|\.\d)",
    r"(?<![\d/.-])(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d\d(?![\d/-]|\.\d)",
    r"(?<![\d/.-])(?:19|20)\d\d[/.-](?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])"
    r"(?:[T ][0-2]\d:[0-5]\d(?::[0-5]\d(?:\.\d+)?)?\s*(?:Z|[+-]\d\d:?\d\d)?)?(?![\d/-]|\.\d)",
    r"(?<![\d/.-])(?:0?[1-9]|1[0-2])/(?:19|20)\d\d(?![\d/-]|\.\d)",
    rf"(?i)\b(?:{HOLIDAYS})(?:,?\s+(?:19|20)\d\d)?\b",
]
# Month/day with no year is only a date after a date-ish word ("seen on 3/14").
DATE_NO_YEAR = (r"(?i)\b(?:on|since|from|until|till|thru|through|by|dated|date|dos|dob|due|seen|admitted|"
                r"discharged|appt|appointment|visit|scheduled|before|after|starting|effective|expires?)\b[:\s]+"
                r"(?P<value>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))(?![\d/])")
BIRTH_CONTEXT = (r"(?i)(?:\bdob\b|\bd\.o\.b\.?|date\s+of\s+birth|birth\s*date|birthdate|birthday|\bborn\b|"
                 r"\bb\.\s*|\bbday\b)[^\n]{0,20}$")

AGE_PATTERNS = [
    r"(?i)\b(?P<value>\d{2,3})\s*(?:-|\s)?\s*(?:years?|yrs?|y/?o|yo)(?:[\s-]*(?:old|of\s+age))?\b",
    r"(?i)\b(?:age[ds]?|aged)\s*[:=]?\s*(?P<value>\d{2,3})\b",
    r"(?i)\b(?P<value>\d{2,3})\s*(?:-|\s)?(?:y\.o\.|yo\b)",
    r"(?i)\b(?:in\s+(?:his|her|their)\s+)(?P<value>9\d|1[01]\d)s\b",
]

URL = r"(?i)\b(?:https?://|ftp://|www\.)[^\s<>\"'()\[\]]*[^\s<>\"'()\[\].,;:!?]"

PHONE = (r"(?<![\w+])(?:(?:\+?1|001)[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-]?)\d{3}[\s.-]?\d{4}"
         r"(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?(?![\w])")
# Numbers read out digit by digit, as speech-to-text writes them: "2-1-2-5-5-5-0-1-4-3", "2 1 2, 5 5 5".
SPOKEN_DIGITS = r"(?<![\w-])\d(?:(?:-|,?[ \t])\d){6,15}(?![\w-])"

FAX_CONTEXT = r"(?i)(?:\bfax\b|\bfacsimile\b|\bf\s*[:#]|\bfx\b)[^\n\d]{0,15}$"

SSN = r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])"
SSN_SPACED = r"(?<![\d ])(?!000|666|9\d\d)\d{3} (?!00)\d{2} (?!0000)\d{4}(?![\d ])"

_MBI_A = "[AC-HJKMNP-RT-Y]"
_MBI_AN = "[AC-HJKMNP-RT-Y0-9]"
MBI = rf"(?<![A-Z0-9])[1-9]{_MBI_A}{_MBI_AN}\d-?{_MBI_A}{_MBI_AN}\d-?{_MBI_A}{_MBI_A}\d\d(?![A-Z0-9])"

DEA = r"(?<![A-Z0-9])[ABCDEFGHJKLMPRSTUX][A-Z9]\d{7}(?![A-Z0-9])"
VIN = r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])"
SOCIAL_HANDLE = r"(?<![\w@./])@[A-Za-z_][A-Za-z0-9_.]{1,29}(?<!\.)(?![\w@])"
LONG_ALNUM = r"(?<![\w-])(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![\w-])"
LONG_DIGITS = r"(?<![\w.,/-])\d{7,}(?![\w,/-]|\.\d)"

HONORIFIC = r"(?:Dr|Mr|Mrs|Ms|Mx|Miss|Prof|Professor|Doctor|Nurse|Rev|Sgt|Capt|Officer|Judge|Atty)"
GREETING = (r"(?:^|\b(?:[Hh]i|[Hh]ello|[Hh]ey|[Dd]ear|[Tt]hanks|[Tt]hank you|[Rr]egards|[Bb]est|[Cc]heers|"
            r"[Ss]incerely|[Ll]ove|[Yy]ours|[Cc]c|[Aa]ttn|[Aa]ttention|[Pp]er|[Aa]sk|[Cc]all|[Tt]ell|[Ww]ith|[Ff]rom|"
            r"[Ff]or|[Tt]o|[Aa]nd|[Pp]atient|[Pp]t|[Mm]om|[Dd]ad|[Ss]on|[Dd]aughter|[Ww]ife|[Hh]usband)\b[,:]?[ \t]+)")

# First names that are also everyday words: not matched on their own.
COMMON_WORD_NAMES = set("""
will may mark bill grace hope faith joy rose june april august art pat sue rob don guy ray sky summer autumn
dawn chase hunter jack frank earnest lane page cash king rich sandy penny ruby amber crystal ivy holly iris lily
daisy violet olive jasmine robin wren sterling price young long little brown black white green gray grey wood
woods hill bell cook baker miller hall ward rice west north south east love best major hart lord christian
carol noel star angel honey sunny storm river brook forest glen dale ford dean drew gene miles wade reed
rocky buck chip duke earl marshal marshall mason page taylor tucker sherman parker porter carter cooper
fisher hunter archer walker turner
""".split())

# Field labels and filler words. An NER name/org/place span made only of these
# (plus tokens or numbers) is a misread, not an identifier: "Email EMAIL_0005",
# "DOB 1948", "Inbox".
LABEL_WORDS = set("""
email e-mail mail phone telephone tel fax cell mobile pager dob d.o.b mrn ssn patient pt dr mr mrs ms miss prof
address addr member id account acct npi dea license lic portal username user login claim case ref reference
visit seen date dated born age aged insurance policy subject re fw fwd from to cc bcc hi hello hey dear thanks
thank regards best sincerely notes note the and of at on in for with by via per attn attention inbox sent
drafts draft archive archives outbox trash junk spam folder folders calendar contacts tasks deleted items
chart record records medical number no num serial device vin plate ip url website web profile handle
follow call called contact contacts emergency spouse husband wife son daughter mother father mom dad
see sons brothers bros associates partners group inc llc ltd co corp corporation company plc pc pllc lp llp
labs lab results result report reports imaging radiology pharmacy billing scheduling referral referrals intake
discharge summary forms form consent claims claim invoice invoices statement statements schedule agenda minutes
update updates meeting meetings team staff admin office reception front desk triage pending approved urgent
""".split())

# Ordinary lowercase words that also appear in the Census name lists; never
# treated as the halves of a lowercase "first last" name.
LOWER_PAIR_BLOCK = set("""
and the will may can see all bill mark pat sue art don rob tim van son man day hope grace joy rose lily
june april august dawn chase hunter jack frank king rich price young long little brown black white green
gray grey wood hill bell cook baker miller hall ward rice west north south east love best major hart lord
care cash case page lane bush park parks rush fox wolf hawk bird bear lamb nurse doctor patient pharmacy
clinic office home house church school bank court field fields lake river spring springs mills mill
call calls phone text email note notes chart labs lab test tests scan visit appointment bills pay paid
stone stones wells banks wise strong sharp swift short small large hardy noble moody gentry
""".split())

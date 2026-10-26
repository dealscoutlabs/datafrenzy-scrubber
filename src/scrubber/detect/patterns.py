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
    "FM": "Federated States of Micronesia", "MH": "Marshall Islands", "PW": "Palau",
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
    "Parade|Frontage Road|Frontage Rd|Frontage|Esplanade|Promenade|Quay|Close|Wynd|Rise|Mews|Chase|Grange|"
    "Subway|Entrance|Nagar|Marg|Chowk|Zila|Colony|Gali|Galli|Ganj|Bazar|Bagh|Puram|"
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
    rf"(?<![\w/-])\d{{1,6}}(?:/\d{{1,5}})?[A-Za-z]?\s+(?:(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West)\.?\s+)?"
    rf"(?:(?:[A-Z0-9][A-Za-z0-9'.-]*|of|the|de|la|del)\s+){{0,4}}(?:{STREET_SUFFIXES}|(?i:{STREET_SUFFIXES}))\b\.?"
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
                r"Glen|Grand|Little|Big|Palm|Cape|Point|Upper|Lower)[ \t]+"
                r"(?!(?:Arm|Arms|Leg|Legs|Extremity|Extremities|Lobe|Lip|Limb|Back|Abdomen|Body|Jaw|Eyelid|Chest|"
                r"Quadrant|Respiratory|Endoscopy|Airway|Teeth|Tooth|Molar|Incisor|Spine|Torso|Thigh)\b)[A-Z][a-z]{2,}"
                r"(?:[ \t]+(?:City|Springs|Falls|Beach|Heights|Hills|Park|Valley|Village|Junction|Center))?\b")
PLACE_SUFFIX = (r"(?i:\b(?:in|at|from|to|near|visiting|outside|around|toward|towards|via|lives|living|moved|"
                r"based|located|resident|residing)[ \t]+(?:in[ \t]+|of[ \t]+)?)"
                r"(?P<value>[A-Z][a-z]+(?:ville|burgh|burg|borough|town|ton|field|mouth|port|view|side|haven|berg|"
                r"chester|shire|stad|bury|ford|land|wood|dale|mont|ridge|brook|crest|furt|fort|haven))\b")

# "Munozshire, XX 98085": a capitalized place, a two-letter code and a ZIP - a city, whatever the code.
CITY_CODE_ZIP = (r"(?P<city>[A-Z][A-Za-z.'-]+(?:[ \t]+[A-Z][A-Za-z.'-]+){0,3}),[ \t]*[A-Z]{2}[ \t]+"
                 r"(?P<zip>\d{5}(?:-\d{4})?)(?![\w-])")

# Firms named after people: "Casey, Gray and Duncan", "Lopez and Sons", "Knight LLC", "Peterson PLC".
FIRM_NAMES = (r"\b[A-Z][a-z]+(?:-[A-Z][a-z]+)?(?:,[ \t]+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)*,?[ \t]+(?:and|&)[ \t]+"
              r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b(?![ \t]+[A-Z][a-z])")  # not "… and Jordan Duarte" (two people)
FIRM_SUFFIX = (r"\b(?:[A-Z][\w'&.-]*[ \t]+){1,4}(?:LLC|L\.L\.C\.|Inc|Incorporated|PLC|Ltd|Limited|LLP|PLLC|PC|"
               r"P\.C\.|Corp|Corporation|Co|Company|Group|Associates|Partners|Brothers|Bros)\b\.?")
HYPHEN_SURNAMES = r"\b[A-Z][a-z]+-[A-Z][a-z]+\b"

LABELED_ZIP = (r"(?i:\b(?:zip|zip ?code|postal(?: ?code)?|post ?code|pin ?code)\b)\s*(?:is\s+)?\**\s*[:#]?\s*\**\s*"
               r"(?P<value>\d{5}(?:-\d{4})?|\d{4,6}|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}|[A-Z]\d[A-Z] ?\d[A-Z]\d)"
               r"(?![\dA-Za-z])")
# Canadian postal code: "K8P 7V8"
CA_POSTCODE = r"(?<![A-Za-z0-9])[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] \d[ABCEGHJ-NPRSTV-Z]\d(?![A-Za-z0-9])"
# UK-style postcode, distinctive enough on its own: "LN0Y 4PT", "SE33 2XX"
UK_POSTCODE = r"(?<![A-Za-z0-9])[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}(?![A-Za-z0-9])"
# GPS coordinates written in text: "56.553299, -26.524318", "23.7698 N, -75.4416 E", "Latitude: 20.8, Longitude: 146.4"
COORDINATES = (r"(?<![\d.])-?\d{1,3}\.\d{4,}\s*°?\s*[NSns]?\s*(?:,\s*|\s+)-?\d{1,3}\.\d{4,}\s*°?\s*[EWew]?(?!\d|\.\d)|"
               r"(?i:lat(?:itude)?)\s*[:=]?\s*-?\d{1,3}\.\d{2,}\s*°?\s*[NSns]?\s*[,;]?\s*(?i:lon(?:g(?:itude)?)?|lng)\s*[:=]?\s*-?\d{1,3}\.\d{2,}")
# card-like groups of four, whether or not the check digit is valid: "4081 4502 8872 0843", "3768-7258-8504-048"
CARD_GROUPS = r"(?<![\d-])\d{4}([ -])\d{4}\1\d{4}\1\d{1,4}(?![\d])|(?<![\d-])\d{4}-\d{4}-\d{4}-(?![\d])"
# 9-19 digits in a row are an identifier (routing number, IMEI, account, card), not an amount
VERY_LONG_DIGITS = r"(?<![\w.,/-])\d{9,19}(?![\w/-]|[.,]\d)"
# UK National Insurance number, with or without spaces: "ZZ 57 31 99 T", "ZZ646920T"
NINO = r"(?<![A-Za-z0-9])[A-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-DT](?![A-Za-z0-9])"
# nine digits grouped 3-3-3 (SIN-style national IDs): "257 340 877"
DIGITS_333 = r"(?<![\d -])\d{3} \d{3} \d{3}(?![\d]| \d)"
# digit groups with two dashes and 8+ digits: plates and IDs ("02-408192-57"), not dates
DASHED_ID = r"(?<![\w-])\d{2,4}-\d{4,8}-\d{2,4}(?![\w-])"
# phone numbers outside North America: "+915229145899", "03-8585-3411", "0414.170.941", "028 9018 0925"
INTL_PHONE2 = (r"(?<![\w+])\+\d{9,15}(?![\w])|"
               r"(?<![\w])0\d{1,4}[ .-]\d{3,4}[ .-]\d{3,4}(?![\w]|[.-]\d)")
# lowercase usernames with digits: "hayes96", "katherine53" (everyday words with a number are excluded)
USERNAME_DIGITS = (r"(?<![\w@./-])(?!(?:covid|base|python|windows|office|iphone|galaxy|pixel|android|html|java|mysql|"
                   r"oauth|ubuntu|debian|centos|redhat|serial|model|version|class|level|room|floor|unit|suite|ward|"
                   r"week|month|year|item|step|phase|stage|grade|type|page|line|table|figure|section|chapter|part|"
                   r"batch|test|sample|tier|zone|route|gate|dock|vitamin|omega|insulin|"
                   r"note|notes|file|files|image|images|scan|scans|photo|photos|report|reports|record|records|chart|"
                   r"form|forms|letter|invoice|export|data|sheet|copy|draft|final|backup|attachment|picture|video|"
                   r"audio|clip|slide|book|volume|issue|case|study|trial|visit|dose|cycle|patient|subject|site|"
                   r"account|order|ticket|frame|layer|track|channel|camera|device|sensor|node|server|host|player)\d)"
                   r"[a-z]{4,}\d{1,3}(?![\w@.-])")
# usernames shaped like "tw_brian740", "emma_1941"
USERNAME_SHAPE = r"(?<![\w@.])[a-z]{2,}_[a-z]*\d{2,}(?![\w@])"
# short coded IDs: "C88614", "EMP248439", "Vh-41273"
SHORT_CODED_ID = r"(?<![\w-])(?:[A-Z]{1,3}-?\d{5,}|[A-Z][a-z]-\d{4,})(?![\w-])"
# international numbers with the area code in brackets: "(02).6295.0291", "(029) 2018 0232", "(08)-9852-4154"
INTL_PHONE = r"(?<![\w(])\(\+?\d{2,4}\)[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w])"
STATE_ZIP = rf"\b(?:{STATE_ABBR})\s+(?P<value>\d{{5}}(?:-\d{{4}})?)(?![\d-])"

WEEKDAY = r"(?:mon|tue|tues|wed|wednes|thu|thur|thurs|fri|sat|satur|sun)(?:day)?\.?"
DATE_PATTERNS = [
    rf"(?i)\b(?:{WEEKDAY},?\s+)?(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?:(?:19|20|21)\d\d|'\d\d))?(?!\d)",
    rf"(?i)\b(?:{WEEKDAY},?\s+)?\d{{1,2}}(?:st|nd|rd|th)?(?:\s+of)?\s+(?:{MONTHS})\.?,?(?:\s+(?:19|20|21)\d\d)?\b",
    rf"(?i)\b(?:{MONTHS})\.?,?\s+(?:(?:19|20|21)\d\d|'\d\d)\b",
    # the same separator both times: "12.5-25" is a dose range, not a date
    r"(?<![\d/.-])(?:0?[1-9]|1[0-2])([/.-])(?:0?[1-9]|[12]\d|3[01])\1(?:(?:19|20|21)\d\d|\d\d)(?![\d/-]|\.\d)",
    r"(?<![\d/.-])(?:0?[1-9]|[12]\d|3[01])([/.-])(?:0?[1-9]|1[0-2])\1(?:19|20|21)\d\d(?![\d/-]|\.\d)",
    r"(?<![\d/.-])(?:19|20|21)\d\d([/.-])(?:0?[1-9]|1[0-2])\1(?:0?[1-9]|[12]\d|3[01])"
    r"(?:[T ][0-2]\d:[0-5]\d(?::[0-5]\d(?:\.\d+)?)?\s*(?:Z|[+-]\d\d:?\d\d)?)?(?![\d/-]|\.\d)",
    r"(?<![\d/.-])(?:0?[1-9]|1[0-2])/(?:19|20|21)\d\d(?![\d/-]|\.\d)",
    rf"(?i)\b(?:{HOLIDAYS})(?:,?\s+(?:19|20|21)\d\d)?\b",
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

# Bare domains ("acme-health.com/portal"): common endings only; "http..."/"www..." links use URL below.
BARE_DOMAIN = (r"(?i)(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|org|net|edu|gov|mil|us|io|co|info|"
               r"biz|health|care|clinic|med|me|ly|ai|app|dev|uk|ca|au|de|fr|in)(?![\w-])(?:/[^\s<>\"']*)?")
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
care coordinator coordinators coordination case manager management services service support health
current facility facilities name names raw identified de-identified deidentified de unit units dept department
data dataset export exports sheet sheets tab tabs table tables contains phi pii raw original copy final draft
allergies allergy medications medication problems problem procedures results encounters encounter immunizations vital signs vitals careplans plan treatment functional status social history assessment reports report section sections summary
extended release delayed oral tablet tablets capsule capsules injection injectable solution suspension syringe prefilled inhaler inhalation metered actuat dose doses chewable topical cream ointment patch transdermal nasal spray vial pen auto-injector cartridge drops ophthalmic otic sublingual buccal vaginal rectal suppository powder granules lozenge gel lotion foam elixir syrup kit pack mg ml mcg hr pf preservative free sodium hydrochloride succinate tartrate acetate sulfate phosphate citrate maleate besylate
recommendations recommendation guideline guidelines goal goals therapy criteria interpretation comment comments reference range enema suppository suppositiry kit packet blister diskus formulary non-formulary repletion fluid fluids miu iu meq mmol umol ng pg dl ul units metacarpal metatarsal phalanx phalanges carpal tarsal humerus femur tibia fibula ulna radius sternum clavicle scapula patella sacrum coccyx vertebra lumbar thoracic cervical normal abnormal manual automated verified repeat repeated confirmed hemolyzed lipemic icteric clotted insufficient quantity positive negative pending final preliminary corrected cancelled canceled duplicate arm arms leg legs forearm upper lower left right proximal distal lateral medial anterior posterior basilic cephalic femoral jugular subclavian radial brachial antecubital hand foot chest abdomen abd thigh knee ankle wrist shoulder hip groin neck scalp trunk back flank pelvis gu gi ent skilled nursing facility rehab hospice transfer emergency room walk-in referral home
child children contrib contribution contributions employer employers employee employees payroll unemployment withholding wage wages tax taxes reconciliation quarterly monthly
nurse nurses rn lpn lvn cna np aprn pa md therapist therapists counselor counselors clinician clinicians physician physicians doctor doctors provider providers aide aides technician tech psychiatrist psychologist pharmacist chaplain dietitian dietician social worker workers director supervisor administrator charge attending resident resident
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

import re

from scrubber.keys import load_or_create_key
from scrubber.state import Store
from scrubber.transform.pseudonym import Pseudonymizer, normalize


def test_same_entity_same_token_across_variants(scrubber):
    p = scrubber.pseudo
    assert p.token("PERSON", "Maria Gonzalez") == p.token("PERSON", "Gonzalez, Maria")
    assert p.token("PERSON", "Maria Gonzalez") == p.token("PERSON", "Dr. Maria Gonzalez, MD")
    assert p.token("PHONE_NUMBER", "(217) 555-0143") == p.token("PHONE_NUMBER", "+1 217.555.0143")
    assert p.token("EMAIL_ADDRESS", "M.G@Example.com") == p.token("EMAIL_ADDRESS", "m.g@example.com")
    assert p.token("PERSON", "Maria Gonzalez") != p.token("PERSON", "Mario Gonzalez")


def test_token_format(scrubber):
    t = scrubber.pseudo.token("ORGANIZATION", "Acme Logistics LLC")
    assert re.fullmatch(r"ORG_\d{4}", t)
    assert scrubber.pseudo.token("ORGANIZATION", "ACME LOGISTICS") == t


def test_consistent_across_documents(scrubber):
    a = scrubber.scrub("Patient Rosalind Achterberg called.").text
    b = scrubber.scrub("Rosalind Achterberg (MRN 5512090) was admitted.").text
    tok = re.search(r"PERSON_\d{4}", a).group(0)
    assert tok in b


def test_secret_generated_locally_once(tmp_path):
    k1 = load_or_create_key(tmp_path)
    k2 = load_or_create_key(tmp_path)
    assert k1 == k2 and len(k1) == 32
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600


def test_different_secret_different_keys(tmp_path):
    s = Store(tmp_path / "s.db")
    cfg = {"pseudonym": {"token_width": 4, "email_domain": "scrubbed.invalid"}}
    a, b = Pseudonymizer(b"a" * 32, s, cfg), Pseudonymizer(b"b" * 32, s, cfg)
    assert a.hkey("PERSON", "x") != b.hkey("PERSON", "x")


def test_mapping_table_stores_only_hmacs(tmp_path):
    s = Store(tmp_path / "s.db")
    cfg = {"pseudonym": {"token_width": 4, "email_domain": "scrubbed.invalid"}}
    Pseudonymizer(b"a" * 32, s, cfg).token("PERSON", "Quentin Applebaum")
    dump = (tmp_path / "s.db").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("s.db-*"))
    assert b"Quentin" not in dump and b"quentin" not in dump


def test_first_name_alias_maps_to_full_name_token(tmp_path):
    s = Store(tmp_path / "s.db")
    cfg = {"pseudonym": {"token_width": 4, "email_domain": "scrubbed.invalid"}}
    p = Pseudonymizer(b"a" * 32, s, cfg)
    p.set_person_aliases([normalize("PERSON", "Ottoline Brackenridge"), normalize("PERSON", "Ottoline Smythe")])
    assert p.token("PERSON", "Brackenridge") == p.token("PERSON", "Ottoline Brackenridge")
    assert p.token("PERSON", "Ottoline") != p.token("PERSON", "Ottoline Brackenridge")  # ambiguous

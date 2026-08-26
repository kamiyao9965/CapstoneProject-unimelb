from __future__ import annotations

import re
from typing import Any


def normalized_name(value: Any) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
    tokens = re.findall(r"[a-z0-9]+", value.casefold().replace("&", " and "))
    # "and" is punctuation-level variation in PHIS labels (for example
    # "chemotherapy, radiotherapy and immunotherapy" versus the database's
    # CamelCase enum).  Removing it makes canonical and display labels share a
    # stable key without discarding meaningful qualifiers such as "with device".
    return "".join(token for token in tokens if token != "and")


_EXTRAS_CANONICAL = {
    "Acupuncture", "AntenatalPostnatal", "Audiology", "ChineseHerbalMedicine",
    "Chiropractic", "DentalGeneral", "DentalMajor", "Dietetics", "Endodontic",
    "ExercisePhysiology", "GlucoseMonitor", "HealthManagement", "HearingAids",
    "HomeNursing", "NonPBS", "OccupationalTherapy", "Optical", "Orthodontic",
    "Orthoptics", "Orthotics", "Osteopathy", "Physiotherapy", "Podiatry",
    "Psychology", "RemedialMassage", "SpeechTherapy", "Vaccinations",
}
_EXTRAS_CANONICAL_BY_KEY = {
    normalized_name(name): name for name in _EXTRAS_CANONICAL
}


_EXTRAS_ALIASES: dict[str, str] = {
    "acupuncture": "Acupuncture",
    "audiology": "Audiology",
    "antenatalclasses": "AntenatalPostnatal",
    "chinesemedicine": "ChineseHerbalMedicine",
    "chinesemedicineconsultation": "ChineseHerbalMedicine",
    "chineseherbalmedicine": "ChineseHerbalMedicine",
    "chiropractic": "Chiropractic",
    "chiropractor": "Chiropractic",
    "dietetics": "Dietetics",
    "dieteticsnutrition": "Dietetics",
    "dietitian": "Dietetics",
    "dietician": "Dietetics",
    "endodontics": "Endodontic",
    "rootcanal": "Endodontic",
    "rootcanaltreatment": "Endodontic",
    "exercisephysiology": "ExercisePhysiology",
    "general dental": "DentalGeneral",
    "generaldental": "DentalGeneral",
    "dentalgeneral": "DentalGeneral",
    "major dental": "DentalMajor",
    "majordental": "DentalMajor",
    "dentalmajor": "DentalMajor",
    "majordentalsurgery": "DentalMajor",
    "majordentalcrownsandbridges": "DentalMajor",
    "majordentaldentures": "DentalMajor",
    "diabeticsupplies": "GlucoseMonitor",
    "hearingaids": "HearingAids",
    "hearingaidsprescribed": "HearingAids",
    "homenursing": "HomeNursing",
    "healthmanagementprograms": "HealthManagement",
    "nonpbspharmaceuticals": "NonPBS",
    "pharmacynonpbs": "NonPBS",
    "pharmacy": "NonPBS",
    "pharmaceutical": "NonPBS",
    "occupationaltherapy": "OccupationalTherapy",
    "optical": "Optical",
    "glassesframes": "Optical",
    "glasseslensessinglevision": "Optical",
    "glasseslensesbifocal": "Optical",
    "glasseslensesmultifocal": "Optical",
    "contactlensestoric": "Optical",
    "contactlensesother": "Optical",
    "orthodontics": "Orthodontic",
    "orthoptics": "Orthoptics",
    "orthoptictreatmentandeyetherapy": "Orthoptics",
    "orthotics": "Orthotics",
    "orthoticsandappliances": "Orthotics",
    "osteopathy": "Osteopathy",
    "physiotherapy": "Physiotherapy",
    "podiatry": "Podiatry",
    "psychology": "Psychology",
    "psychologist": "Psychology",
    "remedialmassage": "RemedialMassage",
    "speechtherapy": "SpeechTherapy",
    "vaccinations": "Vaccinations",
    "serumandvaccine": "Vaccinations",
}

_MAJOR_DENTAL_WORDS = {
    "bridge", "bridges", "crown", "crowns", "denture", "dentures", "implant",
    "implants", "periodontic", "periodontics",
}
_GENERAL_DENTAL_WORDS = {
    "checkup", "examination", "extraction", "fillings", "fluoride", "plaque",
    "scale", "clean", "toothextraction",
}


def canonical_extras_services(value: Any) -> list[str]:
    """Map a raw benefit label to one or more PHI master service names."""
    raw = str(value or "").strip()
    key = normalized_name(raw)
    if not key:
        return []
    if canonical := _EXTRAS_CANONICAL_BY_KEY.get(key):
        return [canonical]
    if "ambulance" in key:
        return []  # Ambulance is not part of the labelled extras service surface.
    for alias, canonical in _EXTRAS_ALIASES.items():
        if normalized_name(alias) == key:
            return [canonical]
    if "chiropractic" in key and "osteopath" in key:
        return ["Chiropractic", "Osteopathy"]
    if "chiro" in key and "osteo" in key:
        return ["Chiropractic", "Osteopathy"]
    if "remedialmassage" in key and "acupuncture" in key:
        return ["RemedialMassage", "Acupuncture"]
    if sum(token in key for token in ("physiotherapy", "chiropractic", "osteopath", "podiatry")) > 1:
        result = []
        for token, canonical in (
            ("physiotherapy", "Physiotherapy"), ("chiropractic", "Chiropractic"),
            ("osteopath", "Osteopathy"), ("podiatry", "Podiatry"),
        ):
            if token in key:
                result.append(canonical)
        return result
    if "orthodont" in key:
        return ["Orthodontic"]
    if "endodont" in key or "rootcanal" in key:
        return ["Endodontic"]
    if "dental" in key:
        if any(normalized_name(word) in key for word in _MAJOR_DENTAL_WORDS):
            return ["DentalMajor"]
        if any(normalized_name(word) in key for word in _GENERAL_DENTAL_WORDS):
            return ["DentalGeneral"]
        return ["DentalGeneral", "DentalMajor"]
    for alias, canonical in sorted(_EXTRAS_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if normalized_name(alias) in key:
            return [canonical]
    return []


_PRODUCT_BRAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("union", "health"),
    ("ahm",),
    ("bupa",),
    ("frank",),
    ("gmhba",),
    ("hci",),
    ("medibank",),
    ("priceline",),
    ("qantas",),
    ("tal",),
)
_PRODUCT_GENERIC_TOKENS = {
    "cover", "excess", "extras", "hospital", "product", "summary",
}
_PRODUCT_CODE_TOKENS = {"bgo", "bse", "d"}
_PRODUCT_EXCESS_AMOUNTS = {"250", "300", "500", "600", "750"}
_PRODUCT_BACK_PERCENTAGES = {"50", "60", "70", "80", "90", "100"}
_PRODUCT_NAME_ALIASES = {
    "starterboostwithsublimits": "smartcarestarterboostwithsublimits",
}


def canonical_product_name(value: Any) -> str:
    """Return a comparison key without cosmetic brand/variant decorators.

    Brand labels, file/product codes, hospital excesses, and percentage-back
    marketing variants are intentionally kept out of the base product name.
    Core tier/family words remain, so genuinely different products do not
    become equal merely because they share a fund or product type.
    """
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
    tokens = re.findall(r"[a-z0-9]+", raw.casefold().replace("&", " and "))
    for prefix in _PRODUCT_BRAND_PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            tokens = tokens[len(prefix):]
            break

    tokens = [token for token in tokens if token not in _PRODUCT_GENERIC_TOKENS]
    tokens = [token for token in tokens if token not in _PRODUCT_CODE_TOKENS]
    tokens = [token for token in tokens if token not in _PRODUCT_EXCESS_AMOUNTS]
    tokens = [
        token
        for token in tokens
        if token != "back"
        and token not in _PRODUCT_BACK_PERCENTAGES
    ]
    tokens = ["family" if token == "families" else token for token in tokens]
    key = "".join(token for token in tokens if token != "and")
    return _PRODUCT_NAME_ALIASES.get(key, key)


_HOSPITAL_ALIASES = {
    "assisted reproductive services": "AssistedReproductive",
    "ear nose and throat": "EarNoseThroat",
    "ear nose throat": "EarNoseThroat",
    "back neck and spine": "BackNeckSpine",
    "bone joint and muscle": "BoneJointMuscle",
    "brain and nervous system": "BrainNervousSystem",
    "breast surgery medically necessary": "BreastSurgery",
    "chemotherapy radiotherapy immunotherapy for cancer": "ChemotherapyRadiotherapyImmunotherapy",
    "diabetes management excluding insulin pumps": "Diabetes",
    "dialysis for chronic kidney failure": "Dialysis",
    "dialysis for chronic kidney disease": "Dialysis",
    "eye not cataracts": "Eye",
    "heart and vascular system": "HeartVascular",
    "hernia and appendix": "HerniaAppendix",
    "hospital psychiatric services": "HospitalPsychiatric",
    "implantation of hearing devices": "ImplantationHearingDevices",
    "kidney and bladder": "KidneyBladder",
    "lung and chest": "LungChest",
    "male reproductive system": "MaleReproductive",
    "miscarriage and termination of pregnancy": "MiscarriageTerminationOfPregnancy",
    "plastic and reconstructive surgery medically necessary": "PlasticReconstructiveSurgery",
    "pregnancy and birth": "PregnancyBirth",
    "tonsils adenoids and grommets": "TonsilsAdenoidsGrommets",
}

_HOSPITAL_CANONICAL = {
    "AssistedReproductive", "BackNeckSpine", "Blood", "BoneJointMuscle",
    "BrainNervousSystem", "BreastSurgery", "Cataracts",
    "ChemotherapyRadiotherapyImmunotherapy", "DentalSurgery", "Diabetes",
    "Dialysis", "DigestiveSystem", "EarNoseThroat", "Eye",
    "GastrointestinalEndoscopy", "Gynaecology", "HeartVascular",
    "HerniaAppendix", "HospitalPsychiatric", "ImplantationHearingDevices",
    "InsulinPumps", "JointReconstructions", "JointReplacements", "KidneyBladder",
    "LungChest", "MaleReproductive", "MiscarriageTerminationOfPregnancy",
    "PainManagement", "PainManagementWithDevice", "PalliativeCare",
    "PlasticReconstructiveSurgery", "PodiatricSurgery", "PregnancyBirth",
    "Rehabilitation", "Skin", "SleepStudies", "TonsilsAdenoidsGrommets",
    "WeightLossSurgery",
}
_HOSPITAL_CANONICAL_BY_KEY = {
    normalized_name(name): name for name in _HOSPITAL_CANONICAL
}


def canonical_hospital_category(value: Any) -> str:
    raw = str(value or "").strip()
    key = normalized_name(raw)
    if canonical := _HOSPITAL_CANONICAL_BY_KEY.get(key):
        return canonical
    for alias, canonical in _HOSPITAL_ALIASES.items():
        if key == normalized_name(alias):
            return canonical
    # Insurer tables often append explanatory coverage notes to an otherwise
    # standard label.  These rules deliberately require the distinctive PHIS
    # phrase so generic surgery/disease text is not over-normalized.
    if "podiatricsurgery" in key and "podiatricsurgeon" in key:
        return "PodiatricSurgery"
    if key.startswith("dialysisforchronickidney"):
        return "Dialysis"
    if all(token in key for token in ("chemotherapy", "radiotherapy", "immunotherapy")):
        return "ChemotherapyRadiotherapyImmunotherapy"
    return raw


def canonical_hospital_categories(value: Any) -> list[str]:
    """Return every canonical PHIS category mentioned in a compact source label."""
    raw = str(value or "").strip()
    if not raw:
        return []
    direct = canonical_hospital_category(raw)
    if direct in _HOSPITAL_CANONICAL:
        return [direct]

    key = normalized_name(raw)
    matches: list[str] = []
    for canonical_key, canonical in _HOSPITAL_CANONICAL_BY_KEY.items():
        if canonical_key in key:
            matches.append(canonical)
    for alias, canonical in _HOSPITAL_ALIASES.items():
        if normalized_name(alias) in key:
            matches.append(canonical)
    if "podiatricsurgery" in key and "podiatricsurgeon" in key:
        matches.append("PodiatricSurgery")
    if "dialysisforchronickidney" in key:
        matches.append("Dialysis")
    if all(token in key for token in ("chemotherapy", "radiotherapy", "immunotherapy")):
        matches.append("ChemotherapyRadiotherapyImmunotherapy")
    return list(dict.fromkeys(matches))

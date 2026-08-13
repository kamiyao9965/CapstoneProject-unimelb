from __future__ import annotations

import re
from typing import Any


def normalized_name(value: Any) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
    return "".join(re.findall(r"[a-z0-9]+", value.casefold().replace("&", " and ")))


_EXTRAS_ALIASES: dict[str, str] = {
    "acupuncture": "Acupuncture",
    "audiology": "Audiology",
    "antenatalclasses": "AntenatalPostnatal",
    "chinesemedicine": "ChineseHerbalMedicine",
    "chineseherbalmedicine": "ChineseHerbalMedicine",
    "chiropractic": "Chiropractic",
    "chiropractor": "Chiropractic",
    "dietetics": "Dietetics",
    "dietitian": "Dietetics",
    "dietician": "Dietetics",
    "endodontics": "Endodontic",
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
    "implants", "periodontic", "periodontics", "rootcanal",
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


def canonical_hospital_category(value: Any) -> str:
    raw = str(value or "").strip()
    key = normalized_name(raw)
    for alias, canonical in _HOSPITAL_ALIASES.items():
        if key == normalized_name(alias):
            return canonical
    return raw

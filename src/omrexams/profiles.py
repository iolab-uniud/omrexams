from copy import deepcopy
from numbers import Integral
from .students import student_condition_mask, validate_student_condition


PROFILE_EXAM_FIELDS = {
    "max_questions", "max_open_questions", "max_total_questions", "page_limits",
    "shuffle_questions", "shuffle_answers", "separate_open_questions",
}


def validate_profiles(config):
    total = config.get("exam", {}).get("max_total_questions")
    if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total < 1):
        raise ValueError("exam.max_total_questions deve essere un intero positivo")
    profiles = config.get("profiles")
    selector = config.get("profile_selector")
    if profiles is None and selector is None:
        return
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles deve contenere almeno un profilo")
    if not isinstance(selector, dict):
        raise ValueError("I profili richiedono profile_selector")
    column = selector.get("column")
    rules = selector.get("rules")
    if rules is None and (not isinstance(column, str) or not column.strip()):
        raise ValueError("Indica la colonna Excel in profile_selector.column")
    if not isinstance(selector.get("default"), str) or selector["default"] not in profiles:
        raise ValueError("Il profilo predefinito non esiste in profiles")
    non_empty = selector.get("non_empty")
    if non_empty is not None and (not isinstance(non_empty, str) or non_empty not in profiles):
        raise ValueError("Il profilo per le celle non vuote non esiste in profiles")
    mapping = selector.get("mapping", {})
    if rules is not None:
        if not isinstance(rules, list):
            raise ValueError("profile_selector.rules deve essere una lista ordinata")
        if column is not None or non_empty is not None or mapping:
            raise ValueError("Le regole ordinate non possono essere combinate con column, mapping o non_empty")
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {"column", "operator", "value", "profile"}:
                raise ValueError("Regola di selezione variante non valida")
            validate_student_condition(rule)
            if not isinstance(rule.get("profile"), str) or rule["profile"] not in profiles:
                raise ValueError("La variante indicata nella regola non esiste")
    if not isinstance(mapping, dict):
        raise ValueError("profile_selector.mapping deve associare valori Excel a profili")
    normalized = {}
    for value, profile in mapping.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError("I valori del mapping devono essere stringhe non vuote")
        if not isinstance(profile, str) or profile not in profiles:
            raise ValueError(f"Profilo inesistente nel mapping: {profile}")
        key = value.strip().casefold()
        if key in normalized and normalized[key] != profile:
            raise ValueError(f"Valore Excel ambiguo nel mapping: {value}")
        normalized[key] = profile
    for name, overrides in profiles.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(overrides, dict):
            raise ValueError("Ogni profilo deve avere un nome e un oggetto di opzioni")
        unknown = set(overrides) - {"exam", "questions", "easyreading", "dyslexia"}
        if unknown:
            raise ValueError(f"Opzioni non supportate nel profilo {name}: {', '.join(sorted(unknown))}")
        exam = overrides.get("exam", {})
        if not isinstance(exam, dict) or set(exam) - PROFILE_EXAM_FIELDS:
            raise ValueError(f"Opzioni exam non supportate nel profilo {name}")
        for field, value in exam.items():
            if field in ("max_questions", "max_open_questions", "max_total_questions", "page_limits"):
                minimum = 1 if field in ("page_limits", "max_total_questions") else 0
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                    raise ValueError(f"{name}.exam.{field} deve essere un intero >= {minimum}")
            elif not isinstance(value, bool):
                raise ValueError(f"{name}.exam.{field} deve essere un booleano")
        for field in ("easyreading", "dyslexia"):
            if field in overrides and not isinstance(overrides[field], bool):
                raise ValueError(f"{name}.{field} deve essere un booleano")
        if "questions" in overrides:
            rules = overrides["questions"]
            if not isinstance(rules, list) or any(
                not isinstance(rule, dict) or not isinstance(rule.get("from"), str)
                for rule in rules
            ):
                raise ValueError(f"Regole domande non valide nel profilo {name}")


def config_for_profile(config, profile):
    if profile not in config.get("profiles", {}):
        raise ValueError(f"Profilo inesistente: {profile}")
    resolved = deepcopy(config)
    overrides = config["profiles"][profile]
    resolved["exam"] = {**resolved.get("exam", {}), **overrides.get("exam", {})}
    for field in ("questions", "easyreading", "dyslexia"):
        if field in overrides:
            resolved[field] = deepcopy(overrides[field])
    return resolved


def select_student_profiles(dataframe, config, first_data_row=2):
    """Resolve profiles without changing the dataframe or its original row indices."""
    validate_profiles(config)
    if not config.get("profiles"):
        return [None] * len(dataframe)
    import pandas as pd

    selector = config["profile_selector"]
    if selector.get("rules") is not None:
        masks = [(rule["profile"], student_condition_mask(dataframe, rule)) for rule in selector["rules"]]
        return [
            next((profile for profile, mask in masks if mask.iloc[position]), selector["default"])
            for position in range(len(dataframe))
        ]
    columns = [str(column).strip().casefold() for column in dataframe.columns]
    column = selector["column"].strip().casefold()
    if column not in columns:
        raise ValueError(f"Colonna profilo '{selector['column']}' non trovata nel file Excel")
    if columns.count(column) != 1:
        raise ValueError(f"Colonna profilo '{selector['column']}' duplicata nel file Excel")
    mapping = {
        value.strip().casefold(): profile
        for value, profile in selector.get("mapping", {}).items()
    }
    profiles = []
    for position, (index, value) in enumerate(dataframe.iloc[:, columns.index(column)].items()):
        key = "" if pd.isna(value) else str(value).strip().casefold()
        if not key:
            profiles.append(selector["default"])
        elif key in mapping:
            profiles.append(mapping[key])
        elif selector.get("non_empty") is not None:
            profiles.append(selector["non_empty"])
        else:
            row = first_data_row + (int(index) if isinstance(index, Integral) else position)
            raise ValueError(f"Valore profilo non riconosciuto alla riga Excel {row}: {value}")
    return profiles


def question_rules(config):
    yield from config.get("questions") or []
    for profile in (config.get("profiles") or {}).values():
        yield from profile.get("questions") or []

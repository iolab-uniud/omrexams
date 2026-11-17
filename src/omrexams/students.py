import pandas as pd


def validate_student_condition(condition):
    column = condition.get("column")
    if not isinstance(column, str) or not column.strip():
        raise ValueError("Indica la colonna Excel da usare per il filtro.")
    operator = condition.get("operator")
    if operator not in ("equals", "contains", "empty", "not_empty"):
        raise ValueError("Condizione del filtro Excel non supportata.")
    value = condition.get("value")
    if value is not None and not isinstance(value, str):
        raise ValueError("Il valore del filtro Excel deve essere una stringa.")
    if operator in ("equals", "contains") and not (value or "").strip():
        raise ValueError("Indica il valore da usare per il filtro Excel.")


def student_condition_mask(dataframe, condition):
    validate_student_condition(condition)
    column = condition["column"].strip().casefold()
    columns = [str(name).strip().casefold() for name in dataframe.columns]
    if column not in columns:
        raise ValueError(f"Colonna filtro '{condition['column']}' non trovata nel file Excel.")
    if columns.count(column) != 1:
        raise ValueError(f"Colonna filtro '{condition['column']}' duplicata nel file Excel.")
    cells = pd.Series(
        ["" if pd.isna(cell) else str(cell).strip().casefold() for cell in dataframe.iloc[:, columns.index(column)]],
        index=dataframe.index, dtype=str,
    )
    operator = condition["operator"]
    value = (condition.get("value") or "").strip().casefold()
    if operator == "empty":
        return cells.eq("")
    if operator == "not_empty":
        return cells.ne("")
    if operator == "equals":
        return cells.eq(value)
    return cells.str.contains(value, regex=False)


def _tabular_header(raw):
    best_row = None
    best_score = -1

    def cells(row):
        return {
            index: value for index, value in enumerate(row)
            if not pd.isna(value) and (not isinstance(value, str) or value.strip())
        }

    for index in range(len(raw)):
        header = cells(raw.iloc[index])
        if len(header) < 2 or not all(isinstance(value, str) for value in header.values()):
            continue
        labels = {position: value.strip().casefold() for position, value in header.items()}
        if len(set(labels.values())) != len(labels):
            continue
        first_column, last_column = min(header), max(header)
        counts = []
        for following in range(index + 1, min(index + 6, len(raw))):
            body = cells(raw.iloc[following])
            named = {position: value for position, value in body.items() if position in header}
            if any(position < first_column or position > last_column for position in body) or len(named) < max(2, len(header) * 0.4):
                break
            repeated = sum(str(value).strip().casefold() == labels[position] for position, value in named.items())
            if repeated >= len(named) / 2:
                break
            counts.append(len(named))
        if counts:
            # Prefer populated table blocks over sparse or repeated preamble rows.
            score = sum(counts) / len(counts) + len(header) * 0.1 + len(counts) * 0.1
            if score > best_score:
                best_row, best_score = index, score
    return best_row


def read_student_dataframe(filename, data_marker=None):
    marker = data_marker or {}
    skip_rows = marker.get("skip_rows") or 0
    column = marker.get("on_column") or 0
    if skip_rows < 0 or column < 0:
        raise ValueError("La riga e la colonna iniziali dell'Excel devono essere non negative")
    skip_until = marker.get("skip_until")
    if skip_until:
        raw = pd.read_excel(filename, header=None)
        if column >= len(raw.columns):
            raise ValueError("La colonna del marcatore Excel non esiste")
        matches = raw.iloc[:, column].astype(str).str.strip() == skip_until
        if not matches.any():
            raise ValueError(f"Marcatore Excel non trovato: {skip_until}")
        skip_rows = int(matches[matches].index[0]) + 1
    elif not skip_rows:
        raw = pd.read_excel(filename, header=None)
        detected = _tabular_header(raw)
        if detected is not None:
            skip_rows = detected
    frame = pd.read_excel(filename, skiprows=skip_rows)
    attempts = 0
    while frame.columns.astype(str).str.startswith("Unnamed:").all() and attempts < 10:
        skip_rows += 1
        frame = pd.read_excel(filename, skiprows=skip_rows)
        attempts += 1
    if not len(frame.columns) or frame.columns.astype(str).str.startswith("Unnamed:").all():
        raise ValueError("Nessuna intestazione Excel leggibile")
    return frame, skip_rows + 2

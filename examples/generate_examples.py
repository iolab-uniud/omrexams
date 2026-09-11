from pathlib import Path
import io
import shutil
import sys

import click
import cv2
import numpy as np
import yaml

from omrexams.correct import Correct
from omrexams.generate import Generate
from omrexams.sort import Sort
from omrexams.utils import qrdecoder


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated"
ANSWER_INDEXES = (0, 1, 1, 2, 2, 3, 3, 0, 2)


def mark_answers(image_path, answer_indexes):
    image = cv2.imread(str(image_path))
    metadata = qrdecoder.decode(image)
    top_left = metadata["top_left"]
    p0 = np.round(metadata["p0"] @ metadata["scaling"]).astype(int) + top_left
    p1 = np.round(metadata["p1"] @ metadata["scaling"]).astype(int) + top_left
    expand_x = int((p1[0] - p0[0]) * 0.05 / 2)
    p0[0] -= expand_x
    p1[0] += expand_x
    roi = image[p0[1]:p1[1], p0[0]:p1[0]]
    _, filled, _ = Correct.detect_circles_edges(roi, metadata)
    pivot = min(filled)
    references = sorted(
        (circle for circle in filled if abs(circle[0] - pivot[0]) <= pivot[2]),
        key=lambda circle: circle[1],
    )

    for reference, answer_index in zip(references, answer_indexes):
        radius = int(metadata["bsize"] * np.max(np.diag(metadata["scaling"])) / 2)
        center_x = int(reference[0] + (answer_index + 1) * 2.5 * radius)
        center_y = reference[1]
        cv2.circle(
            image,
            (int(p0[0] + center_x), int(p0[1] + center_y)),
            radius + 1,
            (0, 0, 0),
            -1,
        )
    if not cv2.imwrite(str(image_path), image):
        raise RuntimeError(f"Cannot write marked page {image_path}")


def generate_engine(engine, paper="A4", output=OUTPUT):
    destination = Path(output) / engine / paper.lower()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    with (ROOT / f"config-{engine}.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    config["basedir"] = str(ROOT)

    prefix = destination / "exam"
    generator = Generate(
        config,
        str(ROOT / "questions"),
        str(prefix),
        students=[("1001", "Ada Lovelace")],
        seed=42,
        paper=paper,
        folded=paper == "A3",
    )
    generator.process()

    sorted_dir = destination / "sorted"
    discarded = Sort(
        [str(prefix.with_suffix(".pdf"))],
        str(sorted_dir),
        str(prefix.with_suffix(".json")),
    ).sort(300, paper)
    expected_discarded = 1
    if len(discarded) != expected_discarded:
        raise RuntimeError(f"Discarded pages for {engine}: {discarded}")

    pages = sorted(sorted_dir.glob("*.png"))
    for page in pages:
        metadata = qrdecoder.decode(cv2.imread(str(page)))
        first, last = metadata["range"]
        if (first, last) == (0, 0):
            continue
        mark_answers(page, ANSWER_INDEXES[first - 1:last])

    previous_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO()
        Correct(
            str(sorted_dir),
            str(destination / "corrected.pdf"),
            str(prefix.with_suffix(".json")),
            300,
            70,
        ).correct()
    finally:
        sys.stdin = previous_stdin

    return destination


@click.command()
@click.option("--engine", type=click.Choice(["latex", "typst", "all"]), default="all")
@click.option("--output", type=click.Path(path_type=Path), default=OUTPUT)
@click.option("--paper", type=click.Choice(["a4", "a3", "all"]), default="all")
def main(engine, output, paper):
    engines = ("latex", "typst") if engine == "all" else (engine,)
    papers = ("A4", "A3") if paper == "all" else (paper.upper(),)
    for selected_engine in engines:
        for selected_paper in papers:
            click.echo(f"Generating {selected_engine} {selected_paper} examples")
            generate_engine(selected_engine, selected_paper, output)


if __name__ == "__main__":
    main()
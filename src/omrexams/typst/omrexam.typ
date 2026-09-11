#import "@preview/cades:0.3.1": qr-code
#import "@preview/mitex:0.2.7": mi, mitex

#let marker-size = 14pt
#let barcode-size = 28mm
#let omr-gray = rgb("888888")

#let registration-mark() = rect(width: marker-size / 2, height: marker-size / 2, fill: black)

#let points(length) = int(calc.round(length / 1pt))

#let page-questions() = {
  let page-number = here().page()
  query(<omr-question>).filter(item => item.location().page() == page-number)
}

#let page-qr-data() = {
  let page-number = here().page()
  let questions = page-questions()
  let qr-width = points(190mm)
  let qr-height = points(277mm)
  if questions.len() == 0 {
    "(405,0)-(405,0)/(" + str(qr-width) + "," + str(qr-height) + ")/14," + str(page-number) + ",0-0"
  } else {
    let positions = questions.map(item => item.location().position().y)
    let top = points(calc.min(..positions) - 10mm - 1.5 * marker-size)
    let bottom = points(calc.max(..positions) - 10mm + 1.5 * marker-size)
    let numbers = questions.map(item => item.value)
    "(405," + str(top) + ")-(540," + str(bottom) + ")/(" + str(qr-width) + "," + str(qr-height) + ")/14," + str(page-number) + "," + str(calc.min(..numbers)) + "-" + str(calc.max(..numbers))
  }
}

#let roi-overlay(enabled) = {
  if enabled {
    let questions = page-questions()
    if questions.len() > 0 {
      let positions = questions.map(item => item.location().position().y)
      let y-top = calc.min(..positions) - 1.5 * marker-size
      let y-bottom = calc.max(..positions) + 1.5 * marker-size
      place(
        top + left,
        dx: 10mm + 405pt,
        dy: y-top,
        rect(width: 135pt, height: y-bottom - y-top, stroke: 2pt + blue),
      )
    }
  }
}

#let bubble(body: none, filled: false) = circle(
  width: marker-size,
  height: marker-size,
  stroke: if filled { none } else { 1pt + omr-gray },
  fill: if filled { black } else { none },
  align(center + horizon, text(fill: if filled { white } else { omr-gray }, size: 8pt, body)),
)

#let omr-row(number, labels) = grid(
  columns: (marker-size, 1fr),
  column-gutter: 4pt,
  align: horizon,
  bubble(body: str(number), filled: true),
  grid(
    columns: (marker-size,) * labels.len(),
    column-gutter: marker-size / 4,
    ..labels.map(label => bubble(body: label)),
  ),
)

#let exam(
  student-id: "",
  student-name: "",
  exam-name: "",
  exam-date: "",
  solution: "None",
  header: none,
  footer: none,
  show-roi: false,
  body,
) = {
  set page(
    paper: "a4",
    margin: (top: 43mm, bottom: 43mm, left: 10mm, right: 62.5mm),
    header: context {
      grid(
        columns: (barcode-size, 1fr, marker-size / 2),
        column-gutter: 2mm,
        align: (left + top, left + top, right + top),
        qr-code(student-id + "," + solution, width: barcode-size, height: barcode-size, error-correction: "H"),
        header,
        registration-mark(),
      )
    },
    footer: context {
      grid(
        columns: (marker-size / 2, 1fr),
        column-gutter: 2mm,
        align: (left + bottom, left + bottom),
        registration-mark(),
        footer,
      )
      place(
        right + bottom,
        dx: 52.5mm,
        qr-code(page-qr-data(), width: barcode-size, height: barcode-size, error-correction: "H"),
      )
    },
    foreground: context { roi-overlay(show-roi) },
  )
  set text(size: 10pt)
  set par(justify: true)

  if exam-name != "" or student-name != "" {
    grid(
      columns: (1fr, auto),
      [*#exam-name*], [#exam-date],
      [#student-name], [#student-id],
    )
    v(0.75em)
  }
  body
}

#let question(number, answers, body) = block(
  width: 100%,
  breakable: false,
)[
  #metadata(number)<omr-question>
  #place(dx: 142.5mm + marker-size / 4, omr-row(number, answers))
  *#number.* #body
]
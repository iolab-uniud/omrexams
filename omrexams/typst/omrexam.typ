#import "@preview/cades:0.3.1": qr-code
#import "@preview/codly:1.3.0": codly-init
#import "@preview/mitex:0.2.7": mi, mitex

#let marker-size = 14pt
#let barcode-size = 28mm
#let crop-margin = 10mm
#let vertical-margin = barcode-size + 15mm
#let answer-area-left = 152.5mm
#let separator-x = answer-area-left - 4mm
#let text-area-height = 297mm - 2 * vertical-margin
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

#let warning-box(body) = box(
  width: 51.5mm,
  inset: 3pt,
  radius: 2pt,
  stroke: 0.6pt + black,
  grid(
    columns: (12pt, 1fr),
    column-gutter: 4pt,
    align: horizon,
    box(width: 10pt, height: 10pt, stroke: 0.6pt + black, inset: 0pt,
      align(center + horizon, text(size: 7pt, weight: "bold", "!"))),
    text(size: 7pt, body),
  ),
)

#let exam-header(header, exam-name, exam-date, student-name, student-id) = block(
  width: separator-x - crop-margin - barcode-size - 2mm,
  height: barcode-size,
  inset: (top: 1pt),
)[
  #set text(size: 8pt)
  #text(size: 11pt, weight: "semibold", header)
  #v(2pt)
  #line(length: 100%, stroke: 0.5pt + omr-gray)
  #v(3pt)
  #grid(
    columns: (1fr, auto),
    row-gutter: 2pt,
    align: (left, right),
    text(weight: "bold", exam-name),
    exam-date,
    student-name,
    student-id,
  )
]

#let page-markers(
  student-data,
  qr-error-correction,
  warning,
  show-roi,
  header,
  exam-name,
  exam-date,
  student-name,
  student-id,
) = {
  place(
    top + left,
    dx: crop-margin,
    dy: crop-margin,
    qr-code(student-data, width: barcode-size, height: barcode-size, error-correction: qr-error-correction),
  )
  place(
    top + left,
    dx: crop-margin + barcode-size + 2mm,
    dy: crop-margin,
    exam-header(header, exam-name, exam-date, student-name, student-id),
  )
  place(top + right, dx: -crop-margin, dy: crop-margin, registration-mark())
  place(bottom + left, dx: crop-margin, dy: -crop-margin, registration-mark())
  place(
    bottom + right,
    dx: -crop-margin,
    dy: -crop-margin,
    qr-code(page-qr-data(), width: barcode-size, height: barcode-size, error-correction: qr-error-correction),
  )
  place(
    top + left,
    dx: separator-x,
    dy: vertical-margin,
    line(
      start: (0pt, 0pt),
      end: (0pt, text-area-height),
      stroke: (paint: omr-gray, thickness: 0.5pt, dash: "dotted"),
    ),
  )
  place(top + left, dx: separator-x, dy: 14mm, warning-box(warning))
  roi-overlay(show-roi)
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
  column-gutter: marker-size / 4,
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
  warning: [*Non scrivere in quest'area!* \ Usa solo per indicare le risposte definitive.],
  qr-error-correction: "H",
  show-roi: false,
  body,
) = {
  show: codly-init.with()
  set page(
    paper: "a4",
    margin: (top: vertical-margin, bottom: vertical-margin, left: 10mm, right: 62.5mm),
    footer: footer,
    foreground: context {
      page-markers(
        student-id + "," + solution,
        qr-error-correction,
        warning,
        show-roi,
        header,
        exam-name,
        exam-date,
        student-name,
        student-id,
      )
    },
  )
  set text(size: 10pt)
  set par(justify: true)
  body
}

#let question(number, answers, title, body) = {
  let has-markers = answers.len() > 0
  block(
    width: 100%,
    breakable: not has-markers,
  )[
    #if has-markers [
      #metadata(number)<omr-question>
      #place(dx: 142.5mm + marker-size / 4, omr-row(number, answers))
    ]
    *#number.* #title
    #body
  ]
}
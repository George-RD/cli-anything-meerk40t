# Design system — Laser job dossier

## Direction

The site is an operator job sheet brought to life: cool workshop stock, registration marks, cut/score/etch notation, calibrated measurement, and a single moving laser head. It avoids the standard dark terminal aesthetic and treats the physical workflow as the visual identity.

## Palette

- `--paper`: cool pale workshop stock.
- `--paper-deep`: denser sheet regions and quiet section contrast.
- `--ink`: near-black green for primary text and outlines.
- `--muted`: ink-tinted secondary text.
- `--signal`: laser orange for the active beam and primary action.
- `--score`: engineering blue for scored geometry.
- `--safe`: calibrated green for verified/offline states.
- `--warning`: warm warning field for operator boundaries.

Color is functional. Orange marks active action or risk; green marks verified/offline state; blue marks score geometry. Secondary text is hue-tinted rather than neutral grey.

## Typography

- Display and body: Bricolage Grotesque, then a robust sans-serif fallback.
- Code, measurements, labels, and command output: Spline Sans Mono, then system monospace.
- Monospace is limited to code and measurement, not used as a general “technical” costume.
- Headings are compact, strongly weighted, and tightly tracked; body copy remains readable at roughly 65–75 characters.

## Composition

The first viewport splits between the offer and a full-scale A3 laser-bed dossier. The visual demonstrates cut, score, and etch paths rather than describing them in generic cards. Later sections alternate dense technical passages with quieter safety and example sections.

Controls use square or clipped geometry, visible focus states, and physical offset shadows. Borders are structural. Cards are only used where the content is genuinely a contained object, such as the terminal or job dossier.

## Motion

One authored motion carries the page: the laser head travels through the hero drawing while cut and score paths resolve. Other motion is restrained to functional state changes. Reduced-motion users receive a complete static drawing.

## Responsive behavior

On narrower screens, the first viewport becomes a single reading sequence: proposition and install actions first, then the dossier. Navigation collapses without hiding the GitHub action. Terminal tabs remain horizontally usable, content never relies on hover, and all primary actions become full-width at phone sizes.

## Accessibility

Semantic landmarks and headings define the page. The command example uses a proper tablist with keyboard arrows, Home, and End. Controls have strong focus states. Text and large display type meet contrast requirements. Copy feedback is announced through a live region. Decorative diagram elements are hidden from assistive technology.

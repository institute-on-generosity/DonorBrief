# DonorBrief style guide

Colors follow IoG Color (26.11.13), the palette that replaced the Brand Guide 2026 lime and green; type, shape and voice below are unchanged from the 2026 guide. Reference copies of the palette PDF and logo files: `~/code/iog-fellowship/design-reference/brand-2026-11/`. Use these values for the email, the edit page, and the archive so they read as one IoG product.

## Color

| Token | Hex | Use |
| --- | --- | --- |
| `blue` | `#00BFFF` | Main accent. Header band, primary buttons, takeaway numbers, chips, accent rules. Always with black text. |
| `red` | `#FF2C00` | Second accent, used sparingly: the "Institute on Generosity" label on black. |
| `dark-blue` | `#324ECD` | From the charting set. Section labels, eyebrows, links, focus outlines on light backgrounds. |
| `black` | `#000000` | Body text, dark bands (top bar, footer), secondary buttons. |
| `gray` | `#F1F5F9` | Cool Gray. Page background. |
| `rule` | `#DCE3EB` | Dividers, borders. A darker step of Cool Gray (not in the palette PDF). |
| `white` | `#FFFFFF` | Cards that sit on cool gray; text on black. |

Quiet text is black at reduced opacity: 62% for secondary text, 45% for meta (dates, sources). In email, where opacity is unreliable, use the solid equivalents `#5E5E5E` and `#8C8C8C`. On black, use white at 60–70%, or the palette's Gray `#979797`.

Don't put Blue or Red text on white or cool gray; contrast is too low. Use Dark Blue for colored text on light backgrounds.

Logo: the archive top bar uses the white IoG logo inline. The email keeps the text label, since it has no hosted image to point to.

## Type

| Role | Font | Size / weight |
| --- | --- | --- |
| Display and headlines | Optima (fallbacks: Optima nova, Candara, Gill Sans, Segoe UI) | Regular (400). Title 30–40px; entry headline 19px. |
| Body | DM Sans (fallback: Segoe UI) | 400; 14.5–16px, line height 1.55. Bold is 700. |
| Eyebrow and section labels | DM Sans | 700, 9.5–11px, uppercase, letter spacing 1.2px, green (or lime on black). |
| Meta (dates, sources, counts) | DM Sans | 400, 11–12.5px, quiet text. |

Optima and Segoe UI are the brand's specified faces; DM Sans loads from Google Fonts. Headlines are never bold.

CSS stacks:

```css
--font-display: Optima, 'Optima nova', Candara, 'Gill Sans', 'Segoe UI', sans-serif;
--font-body: 'DM Sans', 'Segoe UI', Arial, sans-serif;
```

## Shape and spacing

- Corners: 3px on buttons, tags, and cards; 99px for pills.
- Dividers: 1px `rule`. A section label sits above a 1px divider.
- Spacing steps of 4px; generous whitespace between entries (20–24px).
- Flat: no gradients or drop shadows.

## Voice in the interface

Plain language, sentence case for headings and buttons, uppercase only for small labels. Credit sources by name. Every brief carries the line: "AI-drafted from public sources and reviewed by Institute on Generosity staff before sending."

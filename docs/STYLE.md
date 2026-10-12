# DonorBrief style guide

Follows the Institute on Generosity Brand Guide 2026 and the IoG Prompt Library mockup. Use these values for the email, the edit page, and the archive so they read as one IoG product.

## Color

| Token | Hex | Use |
| --- | --- | --- |
| `lime` | `#C7E263` | Brand color. Header band, primary buttons, text highlight. Always with black text. |
| `green` | `#42820F` | Section labels, eyebrows, links, focus outlines. |
| `black` | `#000000` | Body text, dark bands (top bar, footer), secondary buttons. |
| `cream` | `#FBF8F2` | Page background, text on black. |
| `rule` | `#EBE7DF` | Dividers, borders, quiet panels. |
| `white` | `#FFFFFF` | Cards that sit on cream. |

Quiet text is black at reduced opacity: 62% for secondary text, 45% for meta (dates, sources). In email, where opacity is unreliable, use the solid equivalents `#5E5E5E` and `#8C8C8C`. On black, use cream at 60–70%.

Don't put green text on lime or lime text on cream; contrast is too low.

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

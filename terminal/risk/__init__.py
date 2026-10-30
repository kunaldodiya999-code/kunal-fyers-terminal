"""The chart risk desk (owner 30 Oct 2026): draw a Long box on an option chart, the desk sizes it, places a LIMIT
entry when the price reaches the box, keeps ONE SL-L exit order at the exchange, exits at the target by modifying
that same order, and follows every drag of the box. See docs/design/Chart_Risk_Engine_Design.pdf."""

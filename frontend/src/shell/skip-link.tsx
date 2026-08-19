/**
 * The one legitimate raw `href` in the app: a same-page fragment, not a
 * route, so it is exempt from the "build URLs from the route table" rule
 * (see the `no-restricted-syntax` eslint rule in `eslint.config.js`).
 *
 * Must be the first focusable element in the document so a keyboard or
 * screen-reader user can jump past the header navigation (NFR-31).
 */
export function SkipLink() {
  return (
    <a href="#main-content" className="skip-link">
      Skip to main content
    </a>
  );
}

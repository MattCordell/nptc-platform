import { Link } from "@tanstack/react-router";

import { buttonClassName, type ButtonVariant } from "./button-class-name.ts";

/**
 * The way home from a screen that has nothing else to offer (stub,
 * not-found, error, sign-in states). One component so the wording matches
 * everywhere and a screen reader's list of links reads the same.
 */
export function BackToLandingLink({
  variant = "secondary",
}: {
  variant?: ButtonVariant;
}) {
  return (
    <Link to="/" className={buttonClassName(variant)}>
      Back to the landing page
    </Link>
  );
}

import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

type DialogProps = {
  open: boolean;
  onClose: () => void;
  /** Rendered as the dialog's heading and referenced by `aria-labelledby` -
   * every dialog must have one, there is no unlabelled fallback. */
  title: string;
  children: ReactNode;
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(dialog: HTMLDialogElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/**
 * A modal dialog (issue #148). Prefers the native `<dialog>` element's
 * `showModal()` where it is available - top-layer rendering, the implicit
 * `aria-modal="true"`, and a browser-level `Tab` focus trap for free - but
 * does not depend on it for the focus contract: `showModal()`/the browser's
 * own trap are not implemented in jsdom (nor, historically, in every real
 * browser release), so focus-in, `Tab`-trap, and focus-restore are all
 * handled explicitly here. That keeps the contract testable and consistent
 * regardless of what the platform does on top of it.
 */
export function Dialog({ open, onClose, title, children }: DialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<Element | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }

    if (open) {
      triggerRef.current = document.activeElement;
      if (typeof dialog.showModal === "function") {
        try {
          dialog.showModal();
        } catch {
          // jsdom declares showModal but throws "not implemented" - fall
          // through to the plain `open` attribute below either way.
        }
      }
      dialog.setAttribute("open", "");
      getFocusable(dialog)[0]?.focus();
    } else {
      if (typeof dialog.close === "function") {
        try {
          dialog.close();
        } catch {
          // Same jsdom gap as showModal above.
        }
      }
      dialog.removeAttribute("open");
      if (triggerRef.current instanceof HTMLElement) {
        triggerRef.current.focus();
      }
    }
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab") {
        return;
      }

      const focusable = getFocusable(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    // Also handle the browser's own `cancel` event (fired by a native
    // `showModal()` on Escape) so a real browser's trap and this one agree
    // on the same `onClose`, rather than only one of them firing.
    const handleCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };

    dialog.addEventListener("keydown", handleKeyDown);
    dialog.addEventListener("cancel", handleCancel);
    return () => {
      dialog.removeEventListener("keydown", handleKeyDown);
      dialog.removeEventListener("cancel", handleCancel);
    };
  }, [open, onClose]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-6 backdrop:bg-black/50"
    >
      {open ? (
        <>
          <h2 id={titleId} className="text-lg font-semibold text-[var(--color-text)]">
            {title}
          </h2>
          {children}
        </>
      ) : null}
    </dialog>
  );
}

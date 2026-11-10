import { useState } from "react";

// A destructive action takes two clicks: the label, then "Confirm".
export function ConfirmButton({
  label,
  onConfirm,
  disabled,
}: {
  label: string;
  onConfirm: () => void;
  disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <button type="button" className="secondary" disabled={disabled} onClick={() => setArmed(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="actions">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        Confirm {label.toLowerCase()}
      </button>
      <button type="button" className="secondary" onClick={() => setArmed(false)}>
        Cancel
      </button>
    </span>
  );
}

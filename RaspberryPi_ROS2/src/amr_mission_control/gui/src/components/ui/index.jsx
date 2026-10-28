/**
 * UI primitives — Radix behaviour (focus, keyboard, ARIA, portals) styled
 * with the LEVERAGE tokens in styles/app.css.
 */
import { forwardRef } from "react";
import { Tooltip as RTooltip, Select as RSelect, ToggleGroup, Slider as RSlider, AlertDialog, DropdownMenu } from "radix-ui";
import { Check, ChevronDown } from "lucide-react";
import { cx } from "../../lib/cx";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true };

/* ── Button ─────────────────────────────────────────────────────────── */
export const Button = forwardRef(function Button(
  { variant = "secondary", size = "md", icon: Icon, iconRight: IconRight, children, className, type = "button", ...rest },
  ref,
) {
  return (
    <button ref={ref} type={type} className={cx("btn", `btn--${variant}`, `btn--${size}`, !children && "btn--icon", className)} {...rest}>
      {Icon && <Icon {...ICON} />}
      {children != null && <span className="btn__label">{children}</span>}
      {IconRight && <IconRight {...ICON} />}
    </button>
  );
});

/** Icon-only button: always has an accessible name and a tooltip. */
export const IconButton = forwardRef(function IconButton({ label, side = "top", ...rest }, ref) {
  return (
    <Tip label={label} side={side}>
      <Button ref={ref} aria-label={label} {...rest} />
    </Tip>
  );
});

/* ── Tooltip ────────────────────────────────────────────────────────── */
export const TipProvider = ({ children }) => (
  <RTooltip.Provider delayDuration={350} skipDelayDuration={200}>{children}</RTooltip.Provider>
);

export function Tip({ label, side = "top", children }) {
  if (!label) return children;
  return (
    <RTooltip.Root>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content className="tip" side={side} sideOffset={6} collisionPadding={8}>
          {label}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

/* ── Select ─────────────────────────────────────────────────────────── */
export function Select({ value, onValueChange, options, placeholder = "Choose…", id, disabled, "aria-label": ariaLabel }) {
  return (
    <RSelect.Root value={value ?? ""} onValueChange={onValueChange} disabled={disabled}>
      <RSelect.Trigger id={id} className="select" aria-label={ariaLabel}>
        <RSelect.Value placeholder={placeholder} />
        <RSelect.Icon className="select__chevron"><ChevronDown {...ICON} size={14} /></RSelect.Icon>
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content className="menu" position="popper" sideOffset={4} collisionPadding={8}>
          <RSelect.Viewport>
            {options.map((o) => (
              <RSelect.Item key={o.value} value={o.value} disabled={o.disabled} className="menu__item">
                <span className="menu__check"><RSelect.ItemIndicator><Check {...ICON} size={14} /></RSelect.ItemIndicator></span>
                <RSelect.ItemText>{o.label}</RSelect.ItemText>
                {o.hint && <span className="menu__hint">{o.hint}</span>}
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  );
}

/* ── Segmented (single-choice toggle group) ─────────────────────────── */
export function Segmented({ value, onValueChange, options, "aria-label": ariaLabel, size = "md" }) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onValueChange(v)}
      aria-label={ariaLabel}
      className={cx("seg", `seg--${size}`)}
    >
      {options.map((o) => (
        <ToggleGroup.Item key={o.value} value={o.value} className="seg__item" aria-label={o.ariaLabel}>
          {o.icon && <o.icon {...ICON} size={14} />}
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

/* ── Slider ─────────────────────────────────────────────────────────── */
export function Slider({ value, onValueChange, onValueCommit, min, max, step, "aria-label": ariaLabel }) {
  return (
    <RSlider.Root
      className="slider"
      value={[value]}
      min={min}
      max={max}
      step={step}
      onValueChange={(v) => onValueChange(v[0])}
      onValueCommit={(v) => onValueCommit?.(v[0])}
    >
      <RSlider.Track className="slider__track"><RSlider.Range className="slider__range" /></RSlider.Track>
      <RSlider.Thumb className="slider__thumb" aria-label={ariaLabel} />
    </RSlider.Root>
  );
}

/* ── Confirm dialog ─────────────────────────────────────────────────── */
export function ConfirmDialog({ open, onOpenChange, title, body, confirmLabel, onConfirm, tone = "danger" }) {
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="dialog__overlay" />
        <AlertDialog.Content className="dialog">
          <AlertDialog.Title className="dialog__title">{title}</AlertDialog.Title>
          <AlertDialog.Description className="dialog__body">{body}</AlertDialog.Description>
          <div className="dialog__actions">
            <AlertDialog.Cancel asChild><Button variant="secondary">Cancel</Button></AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button variant={tone === "danger" ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

/* ── Dropdown menu ──────────────────────────────────────────────────── */
export const Menu = DropdownMenu.Root;
export const MenuTrigger = DropdownMenu.Trigger;

export function MenuContent({ children, align = "start" }) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content className="menu" align={align} sideOffset={6} collisionPadding={8}>
        {children}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  );
}

export function MenuItem({ icon: Icon, children, hint, tone, ...rest }) {
  return (
    <DropdownMenu.Item className={cx("menu__item", tone && `menu__item--${tone}`)} {...rest}>
      <span className="menu__check">{Icon && <Icon {...ICON} size={15} />}</span>
      <span className="menu__text">
        {children}
        {hint && <span className="menu__sub">{hint}</span>}
      </span>
    </DropdownMenu.Item>
  );
}

export const MenuLabel = ({ children }) => <DropdownMenu.Label className="menu__label">{children}</DropdownMenu.Label>;
export const MenuSeparator = () => <DropdownMenu.Separator className="menu__sep" />;

/* ── Small display pieces ───────────────────────────────────────────── */
export function Kbd({ children }) {
  return <kbd className="kbd">{children}</kbd>;
}

/** The E-JUST red disc: marks what the robot is doing now. */
export function Sun({ live = false, size = 10, className }) {
  return <span className={cx("sun", live && "sun--live", className)} style={{ "--sun": `${size}px` }} aria-hidden="true" />;
}

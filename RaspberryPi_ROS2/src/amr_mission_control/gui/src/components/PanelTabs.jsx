import { Tabs } from "radix-ui";
import { AnimatePresence, motion } from "motion/react";

export default function PanelTabs({ tabs, value, onValueChange, label }) {
  const current = tabs.some((t) => t.id === value) ? value : tabs[0].id;
  return (
    <Tabs.Root className="tabs" value={current} onValueChange={onValueChange}>
      <Tabs.List className="tabs__list" aria-label={label}>
        {tabs.map((t) => (
          <Tabs.Trigger key={t.id} value={t.id} className="tabs__trigger">
            {t.label}
            {t.badge != null && <span className={`tabs__badge num${t.badgeTone ? ` tabs__badge--${t.badgeTone}` : ""}`}>{t.badge}</span>}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={current}
          className="tabs__panel"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.12 }}
        >
          <Tabs.Content value={current} forceMount className="tabs__content">
            {tabs.find((t) => t.id === current).render()}
          </Tabs.Content>
        </motion.div>
      </AnimatePresence>
    </Tabs.Root>
  );
}

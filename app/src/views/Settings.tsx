import { Monitor, Moon, Settings as Gear, Sun } from "lucide-react";
import { useApp } from "../state/app";
import { Field, KV, Segmented } from "../ui/primitives";
import { Panel, View, ViewHead } from "./common";

export function SettingsView() {
  const app = useApp();
  const { prefs, setPrefs } = app;
  return (
    <View>
      <ViewHead icon={<Gear />} title="Settings" subtitle="Stored on this machine only." />
      <Panel title="Appearance">
        <Field label="Theme">
          <Segmented label="Theme" value={prefs.theme} onChange={(theme) => setPrefs({ theme })}
            options={[{ value: "dark", label: "Dark", icon: <Moon /> }, { value: "light", label: "Light", icon: <Sun /> }, { value: "system", label: "System", icon: <Monitor /> }]} />
        </Field>
        <Field label="Density" hint="Compact fits more rows on small screens.">
          <Segmented label="Density" value={prefs.density} onChange={(density) => setPrefs({ density })}
            options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} />
        </Field>
      </Panel>
      <Panel title="Keyboard">
        <KV items={[
          [<kbd>Ctrl K</kbd>, "Command palette: every page, installation, database, session and action"],
          [<kbd>Ctrl 1–6</kbd>, "Overview, Sessions, Databases, Doctor, Docker, Services"],
          [<kbd>Ctrl B</kbd>, "Collapse or expand the sidebar"],
          [<kbd>Ctrl I</kbd>, "Show or hide the inspector"],
          [<kbd>Ctrl J</kbd>, "Show or hide the output panel"],
          [<kbd>Alt ←</kbd>, "Back"],
          [<kbd>↑ ↓</kbd>, "Move through lists and the installation tree; ← → close and open tree nodes"],
          [<kbd>Esc</kbd>, "Close a dialog or the palette (not while a job runs)"],
        ]} />
      </Panel>
      <Panel title="About">
        <KV items={[
          ["Version", app.info?.version ?? "…", "mono"],
          ["User", app.info?.user ?? "…", "mono"],
          ["Group", app.info?.group ? `${app.info.group.group}${app.info.group.active ? " (active)" : " (not active)"}` : "…"],
        ]} />
      </Panel>
    </View>
  );
}

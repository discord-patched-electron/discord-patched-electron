"use strict";
// Add a layout node, not a DOM lookalike. Discord owns rendering and navigation.
module.exports = function settingsEntry(_request, ui) {
  const installed = new WeakSet();
  ui.watch(
    "dpe-openasar-settings",
    (source) => source.includes("LOGOUT_SIDEBAR_ITEM") && /["']user_settings["']/.test(source),
    (exports, runtime) => {
      const scan = ui.inspect({ c: { settings: { exports } } });
      const root = scan.objects.find(
        (value) =>
          ui.safe(value, "key") === "$Root" &&
          ui.safe(value, "analyticsKey") === "user_settings" &&
          typeof ui.safe(value, "buildLayout") === "function",
      );
      if (!root || installed.has(root)) return;
      const icon = ui
        .inspect(runtime)
        .choose(
          "SettingsIcon",
          (signature, names) => names.has("SettingsIcon") && ui.has(signature, "svg"),
        );
      if (!icon) return;
      const build = root.buildLayout;
      root.buildLayout = function (...args) {
        const layout = Reflect.apply(build, this, args);
        if (!Array.isArray(layout)) return layout;
        return layout.map((section) => {
          if (section?.key !== "utility_section" || typeof section.buildLayout !== "function")
            return section;
          return {
            ...section,
            buildLayout(...args) {
              const items = Reflect.apply(section.buildLayout, section, args);
              if (!Array.isArray(items) || items.some((item) => item?.key === "dpe_openasar"))
                return items;
              const index = items.findIndex((item) => item?.key === "logout_sidebar_item");
              if (index < 0) return items;
              const entry = {
                key: "dpe_openasar",
                type: items[index].type,
                useTitle: () => "DPE OpenAsar",
                icon,
                onClick: () => DiscordNative.ipc.send("DISCORD_UPDATED_QUOTES", "o"),
                buildLayout: () => [],
              };
              return [...items.slice(0, index), entry, ...items.slice(index)];
            },
          };
        });
      };
      installed.add(root);
    },
  );
};

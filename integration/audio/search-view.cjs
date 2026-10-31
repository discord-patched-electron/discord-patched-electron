"use strict";

// A small Preact island: the picker controller owns the field value and filtering,
// while this component owns its children. Refresh never replaces the input.
module.exports = function createSearch(document, onInput, icon, runtime) {
  const { h, render, Fragment } = runtime;
  const searchField = document.createElement("div");
  searchField.className = "search-field dpe-input";
  let search, clearSearch;
  function Icon({ name }) {
    return h("span", {
      className: "search-icon",
      "aria-hidden": "true",
      ref: (node) => {
        if (node && !node.firstChild) node.append(icon(name));
      },
    });
  }
  function SearchField({ value }) {
    return h(
      Fragment,
      null,
      h(Icon, { name: "search" }),
      h("input", {
        type: "search",
        placeholder: "Search",
        "aria-label": "Search applications",
        ref: (node) => {
          if (node) search = node;
        },
        onInput: update,
      }),
      h(
        "button",
        {
          type: "button",
          className: "clear-search",
          "aria-label": "Clear search",
          hidden: value.length === 0,
          ref: (node) => {
            if (node) clearSearch = node;
          },
          onClick: () => {
            search.value = "";
            update();
            search.focus();
          },
        },
        h(Icon, { name: "clear" }),
      ),
    );
  }
  function update() {
    const value = search?.value || "";
    render(h(SearchField, { value }), searchField);
    onInput(value);
  }
  render(h(SearchField, { value: "" }), searchField);
  return { search, searchField, clearSearch };
};

module.exports = function desktopExec(filename) {
  return (
    '"' +
    filename.replace(/[\\"`$%]/g, (value) => {
      if (value === "%") return "%%";
      if (value === "\\") return "\\\\\\\\";
      return "\\\\" + value;
    }) +
    '"'
  );
};

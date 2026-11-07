const find = (args) => args.find((arg) => /^discord:/i.test(arg));
let pending = find(process.argv);

module.exports = {
  receive(args) {
    pending = find(args) ?? pending;
  },
  take() {
    const link = pending;
    pending = undefined;
    return link;
  },
};

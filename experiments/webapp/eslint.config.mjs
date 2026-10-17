export default [
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "script",
      globals: {
        window: "readonly", document: "readonly", location: "readonly", history: "readonly", localStorage: "readonly",
        fetch: "readonly", setTimeout: "readonly", clearTimeout: "readonly", requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly", addEventListener: "readonly", innerWidth: "readonly", innerHeight: "readonly",
        confirm: "readonly", structuredClone: "readonly", CSS: "readonly", Node: "readonly", Charts: "writable",
      },
    },
    rules: {
      "no-undef": "error", "no-unused-vars": ["error", { args: "none" }], "no-redeclare": "error",
      "no-dupe-keys": "error", "no-unreachable": "error", "eqeqeq": "error", "no-shadow": "warn",
    },
  },
];

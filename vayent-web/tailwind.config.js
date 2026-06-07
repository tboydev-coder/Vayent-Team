/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: "#4f46e5",
        background: "#0b0b0c",
        primary: "#f3f4f6",
      },
    },
  },
  plugins: [],
};

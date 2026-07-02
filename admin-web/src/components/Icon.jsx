import { icons } from "../icons.js";

export default function Icon({ name }) {
  const svg = icons[name] || "";
  return <span style={{ display: "inline-flex" }} dangerouslySetInnerHTML={{ __html: svg }} />;
}

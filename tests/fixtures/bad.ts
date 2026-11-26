import { exec } from "child_process";

const token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789";

export async function findUser(db: any, name: string): Promise<any> {
  return db.query("SELECT * FROM users WHERE name = '" + name + "'");
}

export function run(cmd: string) {
  exec("ls " + cmd);
  return eval(cmd);
}

export function render(el: HTMLElement, html: string) {
  el.innerHTML = html;
}

export async function loadAll(ids: string[]) {
  const out = [];
  for (const id of ids) {
    const r = await fetch("/api/item/" + id);
    out.push(await r.json());
  }
  return out;
}

export function common(a: number[], b: number[]) {
  const out: number[] = [];
  for (const x of a) {
    if (b.includes(x)) {
      out.push(x);
    }
  }
  return out;
}

export function save(data: unknown) {
  try {
    JSON.stringify(data);
  } catch (e) {}
  console.log("saved");
}

export function later() {
  // TODO: implement
  throw new Error("not implemented");
}

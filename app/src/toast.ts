// 短暂提示。各功能模块都要弹一句，放在这里避免互相从 main 里借。
import { toast } from "./dom";

let toastTimer: number | undefined;

export function showToast(message: string): void {
  toast.textContent = message;
  toast.classList.add("is-visible");
  if (toastTimer !== undefined) {
    window.clearTimeout(toastTimer);
  }
  toastTimer = window.setTimeout(() => {
    toast.classList.remove("is-visible");
  }, 3800);
}

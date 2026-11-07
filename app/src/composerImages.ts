// 输入框待发送图片：粘贴、拖入、移除，发送时由主流程取走。
import type { ImageAttachment } from "./types";
import {
  composer,
  composerAttachments,
  composerShell,
  promptInput,
} from "./dom";
import { showToast } from "./toast";

const IMAGE_MEDIA_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/webp",
  "image/gif",
]);

let pendingImages: ImageAttachment[] = [];

export function parseImage(
  image: Pick<Partial<ImageAttachment>, "media_type" | "data"> | null | undefined,
): ImageAttachment | undefined {
  const mediaType = image?.media_type ?? "";
  const data = image?.data ?? "";
  if (
    !IMAGE_MEDIA_TYPES.has(mediaType) ||
    !data ||
    data.length % 4 !== 0 ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(data)
  ) {
    return undefined;
  }
  return { media_type: mediaType, data };
}

function renderPendingImages(): void {
  pendingImages = pendingImages
    .map(parseImage)
    .filter((image): image is ImageAttachment => Boolean(image));
  composerAttachments.hidden = !pendingImages.length;
  composerAttachments.innerHTML = pendingImages
    .map(
      (image, index) => `
        <figure class="composer-attachment">
          <img src="data:${image.media_type};base64,${image.data}" alt="待发送图片 ${index + 1}" />
          <button type="button" data-remove-image="${index}">移除</button>
        </figure>
      `,
    )
    .join("");
}

async function imageFileToAttachment(file: File): Promise<ImageAttachment | undefined> {
  if (!IMAGE_MEDIA_TYPES.has(file.type)) {
    return undefined;
  }
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(typeof reader.result === "string" ? reader.result : "");
    };
    reader.onerror = () => {
      reject(reader.error ?? new Error(`读取 ${file.name} 失败`));
    };
    reader.readAsDataURL(file);
  });
  const prefix = `data:${file.type};base64,`;
  if (!dataUrl.startsWith(prefix)) {
    return undefined;
  }
  return parseImage({
    media_type: file.type,
    data: dataUrl.slice(prefix.length),
  });
}

export async function addImageFiles(
  files: FileList | File[] | undefined,
): Promise<void> {
  const images = await Promise.all(
    Array.from(files ?? []).map((file) => imageFileToAttachment(file)),
  );
  const valid = images.filter((image): image is ImageAttachment => Boolean(image));
  if (!valid.length) {
    showToast("没有可发送的图片附件");
    return;
  }
  pendingImages = [...pendingImages, ...valid];
  renderPendingImages();
  promptInput.focus();
  showToast(`已添加 ${valid.length} 张图片 · 发送时使用 queue`);
}

export function hasPendingImages(): boolean {
  return pendingImages.length > 0;
}

export function peekPendingImages(): ImageAttachment[] {
  return pendingImages;
}

// 交出发送用的那一份引用，输入框清空。调用方拿到的数组不会被这次清空影响。
export function takePendingImages(): ImageAttachment[] {
  const images = pendingImages;
  pendingImages = [];
  renderPendingImages();
  return images;
}

composerAttachments.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-remove-image]",
  );
  if (button?.dataset.removeImage === undefined) {
    return;
  }
  const index = Number(button.dataset.removeImage);
  if (!Number.isInteger(index) || index < 0 || index >= pendingImages.length) {
    return;
  }
  pendingImages.splice(index, 1);
  renderPendingImages();
});

composer.addEventListener("dragover", (event) => {
  event.preventDefault();
  composerShell.classList.add("is-dragover");
});

composer.addEventListener("dragleave", (event) => {
  if (event.relatedTarget instanceof Node && composer.contains(event.relatedTarget)) {
    return;
  }
  composerShell.classList.remove("is-dragover");
});

composer.addEventListener("drop", (event) => {
  event.preventDefault();
  composerShell.classList.remove("is-dragover");
  void addImageFiles(event.dataTransfer?.files);
});

promptInput.addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
  if (!files.length) {
    return;
  }
  event.preventDefault();
  void addImageFiles(files);
});

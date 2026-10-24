import { ArrowDown, ArrowLeft, ArrowUp, ArrowsClockwise, CaretRight, Check, Copy, FileArrowUp, FileText, MapPin, BellSimple, SidebarSimple, Plus, X } from "@phosphor-icons/react";

const icons = { back: ArrowLeft, check: Check, chevron: CaretRight, close: X, copy: Copy, download: ArrowDown, file: FileText, location: MapPin, phone: BellSimple, panel: SidebarSimple, plus: Plus, refresh: ArrowsClockwise, send: ArrowUp, upload: FileArrowUp };

export function Icon({ name, size = 16 }) {
  const Glyph = icons[name] || FileText;
  return <Glyph size={size} weight="regular" aria-hidden="true" />;
}

"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const menuButtonClass =
  "h-7 rounded-none px-2 text-[13px] text-white outline-none transition-colors hover:bg-white/15 focus:bg-white/15 data-[state=open]:bg-white/20";

export function DesktopMenuBar() {
  return (
    <nav
      aria-label="桌面应用菜单"
      className="flex h-7 shrink-0 items-center bg-[#f59a3d] px-1 text-white"
    >
      <DropdownMenu>
        <DropdownMenuTrigger className={menuButtonClass}>应用</DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={0} className="min-w-40">
          <DropdownMenuItem onSelect={() => void window.hanserDesktop?.openSettings()}>
            模型设置…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void window.hanserDesktop?.quit()}>
            退出
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger className={menuButtonClass}>查看</DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={0} className="min-w-40">
          <DropdownMenuItem onSelect={() => void window.hanserDesktop?.reload()}>
            重新加载
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => void window.hanserDesktop?.toggleFullscreen()}
          >
            切换全屏
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  );
}


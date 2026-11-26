"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const menuButtonClass =
  "h-7 rounded-md px-2 text-[13px] text-foreground transition-colors hover:bg-secondary focus-visible:outline-2 focus-visible:outline-ring data-[state=open]:bg-secondary";

export function DesktopMenuBar() {
  return (
    <nav
      aria-label="桌面应用菜单"
      className="relative z-20 flex h-7 shrink-0 items-center border-b border-border bg-background px-1 text-foreground"
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

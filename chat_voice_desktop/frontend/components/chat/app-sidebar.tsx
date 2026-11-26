"use client";

import { PanelLeftIcon, PenSquareIcon, XIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback } from "react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { SidebarHistory } from "./sidebar-history";

type LocalUser = { id: string };

export function AppSidebar({ user }: { user: LocalUser | undefined }) {
  const router = useRouter();
  const { setOpenMobile, toggleSidebar, isMobile, state } = useSidebar();
  const handleNewChat = useCallback(() => {
    setOpenMobile(false);
    router.push("/");
  }, [router, setOpenMobile]);

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="pb-0 pt-3">
        <SidebarMenu>
          <SidebarMenuItem className="flex flex-row items-center justify-between">
            <SidebarMenuButton
              aria-label={isMobile ? "关闭侧栏" : state === "collapsed" ? "展开侧栏" : "收起侧栏"}
              className="size-8 !px-0 items-center justify-center"
              onClick={toggleSidebar}
              tooltip={state === "collapsed" ? "展开侧栏" : "收起侧栏"}
            >
              {isMobile ? <XIcon className="size-4" /> : <PanelLeftIcon className="size-4" />}
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup className="pt-1">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  className="h-9 rounded-md text-[14px] text-sidebar-foreground hover:bg-sidebar-accent"
                  onClick={handleNewChat}
                  tooltip="新对话"
                >
                  <PenSquareIcon className="size-4 text-primary" />
                  <span className="font-medium">新对话</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarHistory user={user} />
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}

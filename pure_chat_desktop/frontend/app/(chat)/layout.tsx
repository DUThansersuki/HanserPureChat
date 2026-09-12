import { Toaster } from "sonner";
import { AppSidebar } from "@/components/chat/app-sidebar";
import { ChatShell } from "@/components/chat/shell";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { ActiveChatProvider } from "@/hooks/use-active-chat";

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider defaultOpen={false}>
      <AppSidebar user={{ id: "local-user" }} />
      <SidebarInset>
        <Toaster position="top-center" theme="system" />
        <ActiveChatProvider>
          <ChatShell />
        </ActiveChatProvider>
        {children}
      </SidebarInset>
    </SidebarProvider>
  );
}

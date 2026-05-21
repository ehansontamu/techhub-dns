import type { ReactNode } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";

interface SettingsTabsProps {
    overview: ReactNode;
    advanced: ReactNode;
}

export function SettingsTabs({ overview, advanced }: SettingsTabsProps) {
    return (
        <Tabs defaultValue="overview" className="space-y-4">
            <TabsList>
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="advanced">Advanced recovery</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="space-y-6">
                {overview}
            </TabsContent>
            <TabsContent value="advanced" className="space-y-6">
                {advanced}
            </TabsContent>
        </Tabs>
    );
}

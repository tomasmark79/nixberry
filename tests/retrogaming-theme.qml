import QtQuick 2.0

Item {
    Timer {
        interval: 100
        running: true
        repeat: true
        onTriggered: {
            if (api.allGames.count !== 3)
                return;
            for (var i = 0; i < api.allGames.count; ++i) {
                var game = api.allGames.get(i);
                var expected = game.title.indexOf("Freedoom") === 0 ? "doom" : "nes";
                if (game.collections.count !== 1 || game.collections.get(0).shortName !== expected) {
                    console.log("NIXBERRY_CATALOGUE_INVALID", game.title, game.collections.count);
                    stop();
                    return;
                }
            }
            console.log("NIXBERRY_CATALOGUE_VALID");
            stop();
        }
    }
}

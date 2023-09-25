# Discord bot

This is a Discord bot with four functions, mostly aimed at learning Russian. It's highly specific to the sole server the bot is functioning on, I Want To Speak Russian, but the code is sufficiently generic that you should have no problem adapting it to your circumstances.

For me personally, making this bot was also an exercise in defensive programming. As of the time of writing the bot has been functioning for over a year, serving up thousands of "games" without a single hitch or crash.

## Wordle

The original function of this bot was to play Wordle with you. You simply have to type your guesses into the chat and it'll respond according to Wordle rules. Like all the following games, it's on an endless loop.

## SoftSign

Determining the gender of a Russian word isn't that hard, except if the word ends in a soft consonant, then all bets are off. This game spawns messages with interactive buttons that allow you to test your knowledge.

## Spelling

This function is based on the online game "Море Слов". The New York Times also features a game similar to this, "Spelling Bee". In this game, you're given a root word, and you have to find all words that can be made with the letters of this word, using each letter at most once. The game ends when every common word has been found, i.e. every word that is in the top twenty thousand most commonly used Russian words, or after 24 hours. 

The game divides the players in two teams, natives (of Russian) and learners, depending on their flair.

## BumpReminder

The server this bot is residing in subscribes to a few Discord server lists. One's presence on these lists is kept active by "bumping" the server regularly. This bot reminds you of that task. I spent way too much effort parsing `Server Monitoring`'s bump message, which only includes a time if you bump before the deadline. Ah well.

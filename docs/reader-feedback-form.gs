/**
 * Builds the DonorBrief reader/tester feedback Google Form.
 *
 * How to run:
 *   1. Go to https://script.google.com and click "New project".
 *   2. Delete the sample code, paste this whole file, and click Save.
 *   3. Pick `createDonorBriefFeedbackForm` in the function menu and click Run.
 *      Approve the permission prompt (it only needs access to create a form).
 *   4. Open the Execution log. It prints the edit link and the link to send to
 *      testers. The form is also in your Google Drive.
 *
 * Running it again makes a second, separate form.
 */
function createDonorBriefFeedbackForm() {
  var form = FormApp.create('DonorBrief: Reader Feedback');
  form.setDescription(
    'Thanks for reading DonorBrief, the Institute on Generosity\'s weekly briefing on ' +
    'philanthropy news. This form takes about 5 minutes. Every question is optional ' +
    'unless marked.'
  );
  form.setCollectEmail(false);
  form.setAllowResponseEdits(true);
  form.setProgressBar(true);
  form.setConfirmationMessage(
    'Thank you! Your feedback goes straight to the team building DonorBrief.'
  );

  var agree = ['Strongly disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly agree'];

  // ---- Page 1: About you ----
  form.addSectionHeaderItem().setTitle('About you');

  form.addMultipleChoiceItem()
    .setTitle('Which best describes you?')
    .setChoiceValues(['Nonprofit staff or leader', 'Funder or foundation staff',
                      'Donor or philanthropic advisor', 'Researcher or academic',
                      'IoG staff or fellow'])
    .showOtherOption(true);

  form.addTextItem()
    .setTitle('Which issue(s) did you read? (date or subject line)');

  form.addMultipleChoiceItem()
    .setTitle('Where did you read it?')
    .setChoiceValues(['Email on a phone', 'Email on a laptop or desktop',
                      'Web archive page']);

  form.addMultipleChoiceItem()
    .setTitle('How much of it did you read?')
    .setChoiceValues(['Just the key takeaways', 'Takeaways plus a few stories',
                      'Most of it', 'All of it']);

  // ---- Page 2: The top of the brief ----
  form.addPageBreakItem().setTitle('Key takeaways and "What\'s building"');

  form.addGridItem()
    .setTitle('How much do you agree?')
    .setRows([
      'The key takeaways gave me the point in about 30 seconds',
      'Each takeaway explained why it matters',
      '"What\'s building" spotted trends I hadn\'t noticed',
      'The trends felt backed up by the stories cited',
      'The takeaways were relevant to my work'
    ])
    .setColumns(agree);

  form.addParagraphTextItem()
    .setTitle('Was any takeaway or trend off-base, overstated, or obvious?');

  // ---- Page 3: The news ----
  form.addPageBreakItem().setTitle('The news stories');

  form.addGridItem()
    .setTitle('How much do you agree?')
    .setRows([
      'The stories were relevant to philanthropy and giving',
      'The summaries were clear and easy to read',
      'The summaries were accurate to the original articles',
      'The links went where I expected',
      'There was little repetition from earlier weeks',
      'The sections were organized sensibly'
    ])
    .setColumns(agree);

  form.addMultipleChoiceItem()
    .setTitle('How was the length?')
    .setChoiceValues(['Too short', 'About right', 'Too long']);

  form.addMultipleChoiceItem()
    .setTitle('Did you spot any errors (wrong facts, numbers, names, or broken links)?')
    .setChoiceValues(['No', 'Yes', 'Not sure']);

  form.addParagraphTextItem()
    .setTitle('If yes, which story and what was wrong?');

  form.addParagraphTextItem()
    .setTitle('What topics or sources are missing that you\'d want covered?');

  form.addParagraphTextItem()
    .setTitle('What could we cut?');

  // ---- Page 4: Overall ----
  form.addPageBreakItem().setTitle('Overall');

  form.addScaleItem()
    .setTitle('Overall, how would you rate DonorBrief?')
    .setBounds(1, 5)
    .setLabels('Poor', 'Excellent')
    .setRequired(true);

  form.addScaleItem()
    .setTitle('How likely are you to recommend it to a friend or colleague?')
    .setBounds(0, 10)
    .setLabels('Not at all likely', 'Extremely likely');

  form.addMultipleChoiceItem()
    .setTitle('Would you keep reading it every week?')
    .setChoiceValues(['Yes', 'Maybe', 'No']);

  form.addMultipleChoiceItem()
    .setTitle('Compared with other philanthropy newsletters you read, it is…')
    .setChoiceValues(['More useful', 'About the same', 'Less useful',
                      'I don\'t read others']);

  form.addParagraphTextItem()
    .setTitle('What was most valuable?');

  form.addParagraphTextItem()
    .setTitle('Did the email display badly anywhere? (layout, images, formatting)');

  form.addParagraphTextItem()
    .setTitle('Anything else?');

  form.addTextItem()
    .setTitle('Email, if you\'re happy for us to follow up (optional)');

  Logger.log('Edit the form:  ' + form.getEditUrl());
  Logger.log('Send to testers: ' + form.getPublishedUrl());
}

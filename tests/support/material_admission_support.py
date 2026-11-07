"""Shared material admission fixtures and assertions; no collected scenarios."""



def reviewed_examples(study, item):
    # These inert records stand in for the independently reviewed bundled
    # registry. No AI acceptance flag is treated as example qualification.
    study.examples.bundled['examples'] = [{'id': f'ex:{i}', 'text': f'試験語の例{i}。'} for i in range(10)]
    study.examples.bundled['references'] = [{'id': f'ref:reviewed:{i}', 'example_id': f'ex:{i}',
                                            'target_id': item['id']} for i in range(10)]
